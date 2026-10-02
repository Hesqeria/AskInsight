"""Eval utility primitives (NL2SQL accuracy + usability metrics).

Pure functions, no I/O - unit-testable. Higher-level runners compose
these against real Doris / LangSmith traces.

Three layers of accuracy:
  1. SQL validity   - parses + executes without error
  2. EX equivalence - same result set as gold (order-insensitive)
  3. AST similarity - structural match (catches EX false-negatives
                      when SQL semantically differs but happens to
                      return same rows on small test data)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional


# --------------------------------------------------------------------------- #
# SQL parsing & normalization
# --------------------------------------------------------------------------- #
_TABLE_REF_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+'
    r'(?:`?\w+`?\.)?'         # optional schema
    r'`?(?P<table>[A-Za-z_][\w]*)`?',
    re.IGNORECASE,
)


def extract_referenced_tables(sql: str) -> set[str]:
    """Cheap regex-based table extraction. Good enough for F1 scoring
    at our schema-linking granularity; we don't need a full AST here."""
    if not sql:
        return set()
    # Strip string literals first so we don't pick up "FROM" inside them.
    cleaned = re.sub(r"'[^']*'", "''", sql)
    cleaned = re.sub(r'"[^"]*"', '""', cleaned)
    return {m.group("table").lower() for m in _TABLE_REF_RE.finditer(cleaned)}


# Columns we should NOT count as "referenced" for schema linking F1.
# These are SQL built-ins / pseudo-columns, not real schema columns.
_NON_COLUMN_TOKENS = frozenset({
    "*", "count", "sum", "avg", "min", "max", "distinct", "as",
    "and", "or", "not", "null", "true", "false", "in", "between",
    "like", "is", "case", "when", "then", "else", "end", "exists",
    "all", "any", "union", "intersect", "except", "asc", "desc",
    "limit", "offset", "cast", "convert", "date", "year", "month",
    "day", "hour", "minute", "second", "now", "current_date",
    "current_timestamp", "if", "ifnull", "coalesce", "concat",
    "substring", "length", "trim", "lower", "upper", "round",
    "ceil", "floor", "abs", "row_number", "rank", "dense_rank",
    "over", "partition", "interval", "from_unixtime", "unix_timestamp",
    "date_format", "str_to_date", "group_concat",
})

_COLUMN_RE = None  # deprecated - kept for backward import compat; tokenizer is used now


def extract_referenced_columns(sql: str) -> set[str]:
    """Cheap tokenizer-based column extraction for schema-linking F1.

    Tokenizes the SQL (after stripping strings/numbers/table-refs) and
    keeps identifiers that aren't SQL keywords, aggregates, or aliases.

    Conservative: avoids the FROM clause keyword as a column, skips known
    SQL functions/aggregates. Returns bare column names (no table prefix)
    in lowercase. Precision favors RAG diagnostics over recall.

    For exact column extraction, use `schema_linking_f1_columns_ast`
    which goes through sqlglot.
    """
    if not sql:
        return set()
    # Strip string + number literals + comments.
    cleaned = _strip_strings_and_comments(sql)
    cleaned = re.sub(r"\b\d+(?:\.\d+)?\b", "0", cleaned)
    # Strip table refs (FROM x, JOIN y) so we don't pick up table names as cols.
    cleaned = _TABLE_REF_RE.sub(" ", cleaned)
    # Strip alias.column forms: keep only the column part.
    cleaned = re.sub(r"\b[a-zA-Z_]\w*\.", "", cleaned)

    # Tokenize: identifiers + single-char punctuation.
    tokens = re.findall(r"[a-zA-Z_]\w*|[^\s\w]", cleaned)

    # SQL keywords to exclude (in addition to _NON_COLUMN_TOKENS).
    _KEYWORDS = {
        "select", "where", "from", "join", "on", "group", "order",
        "having", "by", "left", "right", "inner", "outer", "full",
        "cross", "with", "into", "update", "set", "values", "insert",
        "delete", "union", "intersect", "except", "asc", "desc",
        "limit", "offset", "distinct", "all", "as", "and", "or",
        "not", "null", "is", "in", "between", "like", "case", "when",
        "then", "else", "end", "exists", "any", "some", "true", "false",
        "partition", "over", "rows", "range", "preceding", "following",
        "current", "row",
    }

    cols = set()
    for tok in tokens:
        if not tok or not tok[0].isalpha() and tok[0] != "_":
            continue  # punctuation
        lower = tok.lower()
        if lower in _NON_COLUMN_TOKENS or lower in _KEYWORDS:
            continue
        # Skip single-char tokens that are probably table aliases (o, u, t...).
        if len(tok) == 1:
            continue
        cols.add(lower)
    return cols


def normalize_sql(sql: str) -> str:
    """Cheap normalization so trivially-different SQL compares equal:
    lowercase, collapse whitespace, strip trailing semicolons + comments.
    For full AST equivalence use `ast_similarity`."""
    if not sql:
        return ""
    s = sql.strip().rstrip(";")
    # Strip line comments.
    s = "\n".join(line for line in s.splitlines() if not line.strip().startswith("--"))
    # Collapse whitespace.
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


# --------------------------------------------------------------------------- #
# Result-set equivalence (the EX metric)
# --------------------------------------------------------------------------- #
def _row_to_hashable(row) -> tuple:
    """Make a DB row hashable + order-insensitive at the column level.

    We sort the row's values so SELECT col_a, col_b vs SELECT col_b, col_a
    with same data still counts as a match. This is more lenient than
    Spider's strict EX but matches BIRD's "value-equivalence" mode.

    To make sorting deterministic across mixed types (None/int/float/str),
    we tag each value with a type-rank prefix. Numeric values are rounded
    to 4 decimal places so 100.00004 ≈ 100.0; tighter than that is treated
    as a real difference."""
    TYPE_RANK = {None: 0, float: 1, int: 1, str: 2}

    def _key(v):
        if v is None:
            return (0, "")
        if isinstance(v, (int, float)):
            # Round to 4dp for float tolerance.
            return (1, round(float(v), 4))
        return (2, str(v).strip().lower())

    # `sorted` on the (rank, value) tuples is well-defined across types.
    return tuple(sorted(_key(v) for v in row))


def result_sets_equal(
    gold_rows: list[tuple], pred_rows: list[tuple],
    order_matters: bool = False,
) -> bool:
    """Compare two result sets for equivalence.

    Args:
        gold_rows / pred_rows: lists of tuples (DB API fetchall output).
        order_matters: True for ORDER BY queries; False (default) treats
                       the result as a set/multiset.

    Returns:
        True iff the result sets are equivalent.
    """
    if len(gold_rows) != len(pred_rows):
        return False
    if len(gold_rows) == 0:
        return True

    g = [_row_to_hashable(r) for r in gold_rows]
    p = [_row_to_hashable(r) for r in pred_rows]

    if order_matters:
        return g == p

    # Order-insensitive: use sorted comparison (handles duplicates correctly).
    return sorted(g) == sorted(p)


# --------------------------------------------------------------------------- #
# AST similarity (fallback when EX is too strict)
# --------------------------------------------------------------------------- #
def ast_similarity(gold_sql: str, pred_sql: str) -> float:
    """Structural similarity score in [0, 1].

    Uses sqlglot if available (full AST diff); falls back to token
    Jaccard if not. The score helps distinguish:
      - 1.0  SQL parses to identical AST
      - ~0.5 same tables/columns, different predicates
      - 0.0  no structural overlap
    """
    try:
        import sqlglot
        from sqlglot import diff as ast_diff
        try:
            g_tree = sqlglot.parse_one(gold_sql, read="mysql")
            p_tree = sqlglot.parse_one(pred_sql, read="mysql")
            if g_tree is None or p_tree is None:
                return 0.0
            if g_tree.sql() == p_tree.sql():
                return 1.0
            # Count edits needed (capped) and convert to similarity.
            edits = ast_diff(g_tree, p_tree)
            n_nodes = max(len(list(g_tree.walk())), len(list(p_tree.walk())), 1)
            # More edits = lower similarity, clamped to [0, 1].
            return max(0.0, 1.0 - len(edits) / n_nodes)
        except Exception:
            return _token_jaccard(gold_sql, pred_sql)
    except ImportError:
        return _token_jaccard(gold_sql, pred_sql)


def _token_jaccard(a: str, b: str) -> float:
    """Fallback: token-level Jaccard similarity (0-1)."""
    if not a or not b:
        return 0.0
    ta = set(normalize_sql(a).split())
    tb = set(normalize_sql(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# --------------------------------------------------------------------------- #
# Schema linking F1
# --------------------------------------------------------------------------- #
@dataclass
class F1Result:
    precision: float
    recall: float
    f1: float

    def to_dict(self) -> dict:
        return {"precision": round(self.precision, 4),
                "recall": round(self.recall, 4),
                "f1": round(self.f1, 4)}


def schema_linking_f1(
    gold_tables: Iterable[str], pred_tables: Iterable[str],
) -> F1Result:
    """F1 over table-set selection. Treats 'select the right tables' as
    a multiset problem: P = |gold ∩ pred| / |pred|, R = |gold ∩ pred| / |gold|."""
    g = {t.lower() for t in gold_tables if t}
    p = {t.lower() for t in pred_tables if t}
    if not g and not p:
        return F1Result(1.0, 1.0, 1.0)
    if not p:
        return F1Result(0.0, 0.0, 0.0)
    if not g:
        return F1Result(0.0, 0.0, 0.0)
    tp = len(g & p)
    precision = tp / len(p)
    recall = tp / len(g)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return F1Result(precision, recall, f1)


def schema_linking_f1_columns(
    gold_sql: str, pred_sql: str,
) -> F1Result:
    """Column-level Schema Linking F1.

    Most diagnostic variant: tells you whether the RAG layer is recalling
    the right columns. Low precision = hallucinated columns; low recall =
    missing required columns. Pairs with `schema_linking_f1` (table-level)
    for the full RAG-quality picture described in NL2SQL其他测评方法-PRD.md
    FR-METHOD-03.
    """
    g = extract_referenced_columns(gold_sql)
    p = extract_referenced_columns(pred_sql)
    return schema_linking_f1(g, p)


# --------------------------------------------------------------------------- #
# Component matching (per-clause scoring)
# --------------------------------------------------------------------------- #
@dataclass
class ComponentScores:
    """Per-clause F1 score in [0, 1] (NL2SQL其他测评方法-PRD.md FR-METHOD-02).

    0.0 = totally wrong, 1.0 = perfect match for that clause. `overall`
    is a simple mean (no weighting by default — let downstream consumers
    weight as needed).
    """
    select: float = 0.0
    from_: float = 0.0       # FROM/JOIN tables
    where: float = 0.0
    group_by: float = 0.0
    having: float = 0.0
    order_by: float = 0.0

    @property
    def overall(self) -> float:
        s = [self.select, self.from_, self.where,
             self.group_by, self.having, self.order_by]
        return sum(s) / len(s) if s else 0.0

    def to_dict(self) -> dict:
        return {
            "select": round(self.select, 3),
            "from": round(self.from_, 3),
            "where": round(self.where, 3),
            "group_by": round(self.group_by, 3),
            "having": round(self.having, 3),
            "order_by": round(self.order_by, 3),
            "overall": round(self.overall, 3),
        }


def _f1_from_sets(g: set, p: set) -> float:
    if not g and not p:
        return 1.0
    if not g or not p:
        return 0.0
    tp = len(g & p)
    if tp == 0:
        return 0.0
    prec = tp / len(p)
    rec = tp / len(g)
    return 2 * prec * rec / (prec + rec)


def component_match(gold_sql: str, pred_sql: str) -> ComponentScores:
    """Score SQL by component: SELECT / FROM / WHERE / GROUP BY /
    HAVING / ORDER BY. Returns F1 per component.

    Implementation note: uses regex extraction (fast, no AST needed for
    most cases). For SQL with deeply nested subqueries, the per-clause
    view flattens inner clauses into the outer ones — acceptable for
    diagnostic scoring.
    """
    g_lower = " " + _strip_strings_and_comments(gold_sql).lower() + " "
    p_lower = " " + _strip_strings_and_comments(pred_sql).lower() + " "

    # SELECT cols: between SELECT and FROM
    def _select_cols(s):
        m = re.search(r"\bselect\b(.*?)(?:\bfrom\b|$)", s, re.DOTALL)
        if not m:
            return set()
        # Strip "DISTINCT", "ALL", aggregate wrappers (rough).
        body = m.group(1)
        body = re.sub(r"\b(?:distinct|all)\b", " ", body)
        return {c.strip() for c in body.split(",") if c.strip() and c.strip() != "*"}

    # FROM/JOIN tables
    def _from_tables(s):
        m = re.search(r"\bfrom\b(.*?)(?:\bwhere\b|\bgroup\b|\border\b|\bhaving\b|\blimit\b|$)",
                      s, re.DOTALL)
        if not m:
            return set()
        body = m.group(1)
        # Extract identifiers after FROM/JOIN, drop JOIN keywords/ON clauses.
        body = re.sub(r"\bon\b.*?(?=(?:\bjoin\b|$))", " ", body, flags=re.DOTALL)
        toks = re.findall(r"\b[a-zA-Z_]\w*\b", body)
        return {t for t in toks if t not in
                {"join", "inner", "left", "right", "outer", "full",
                 "cross", "as", "on", "using", "natural"}}

    # WHERE/HAVING body: normalize whitespace and compare as token sets
    def _clause_body(s, kw_start, kw_stops):
        pat = rf"\b{kw_start}\b(.*?)(?={'|'.join(kw_stops)}|$)"
        m = re.search(pat, s, re.DOTALL)
        return m.group(1).strip() if m else ""

    def _where_body(s):
        return _clause_body(s, "where",
                            [r"\bgroup\b", r"\border\b", r"\bhaving\b",
                             r"\blimit\b", r"\bunion\b"])

    def _having_body(s):
        return _clause_body(s, "having",
                            [r"\border\b", r"\blimit\b", r"\bunion\b"])

    def _group_cols(s):
        m = re.search(r"\bgroup\s+by\b(.*?)(?:\bhaving\b|\border\b|\blimit\b|$)",
                      s, re.DOTALL)
        if not m:
            return set()
        return {c.strip() for c in m.group(1).split(",") if c.strip()}

    def _order_cols(s):
        m = re.search(r"\border\s+by\b(.*?)(?:\blimit\b|$)", s, re.DOTALL)
        if not m:
            return set()
        # Strip ASC/DESC, take just column expressions.
        body = re.sub(r"\b(?:asc|desc)\b", " ", m.group(1))
        return {c.strip() for c in body.split(",") if c.strip()}

    # Token-level F1 for free-form clauses (WHERE, HAVING): order-insensitive.
    def _token_f1(g_body, p_body):
        if not g_body and not p_body:
            return 1.0
        if not g_body or not p_body:
            return 0.0
        gt = set(re.findall(r"\w+", g_body))
        pt = set(re.findall(r"\w+", p_body))
        # Drop operator-only noise.
        return _f1_from_sets(gt, pt)

    scores = ComponentScores(
        select=_f1_from_sets(_select_cols(g_lower), _select_cols(p_lower)),
        from_=_f1_from_sets(_from_tables(g_lower), _from_tables(p_lower)),
        where=_token_f1(_where_body(g_lower), _where_body(p_lower)),
        group_by=_f1_from_sets(_group_cols(g_lower), _group_cols(p_lower)),
        having=_token_f1(_having_body(g_lower), _having_body(p_lower)),
        order_by=_f1_from_sets(_order_cols(g_lower), _order_cols(p_lower)),
    )
    return scores


def _strip_strings_and_comments(sql: str) -> str:
    """Remove string literals, line comments, block comments — keep keywords."""
    if not sql:
        return ""
    s = re.sub(r"'[^']*'", "''", sql)
    s = re.sub(r'"[^"]*"', '""', s)
    s = re.sub(r"--[^\n]*", " ", s)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    return s


# --------------------------------------------------------------------------- #
# Cost efficiency ($/correct answer)
# --------------------------------------------------------------------------- #
def cost_efficiency(
    n_correct: int,
    tokens_in: int,
    tokens_out: int,
    price_in_per_1k: float,
    price_out_per_1k: float,
) -> float | None:
    """¥ spent per correct answer (NL2SQL其他测评方法-PRD.md FR-METHOD-07).

    Args:
        n_correct: number of correct answers in the run.
        tokens_in / tokens_out: cumulative token counts for the run.
        price_in_per_1k / price_out_per_1k: ¥ per 1k tokens
            (e.g. Qwen3 0.0003, deepseek-chat 0.001, gpt-4o-mini 0.00015).

    Returns:
        Cost ¥ per correct answer, or None if n_correct == 0.
    """
    if n_correct <= 0:
        return None
    cost = (tokens_in / 1000.0) * price_in_per_1k \
         + (tokens_out / 1000.0) * price_out_per_1k
    return round(cost / n_correct, 6)


# --------------------------------------------------------------------------- #
# Difficulty classification (PRD-friendly buckets)
# --------------------------------------------------------------------------- #
DIFFICULTY_EASY = "easy"
DIFFICULTY_MEDIUM = "medium"
DIFFICULTY_HARD = "hard"


def classify_difficulty(sql: str) -> str:
    """Heuristic difficulty bucket matching BIRD's taxonomy:
      - easy:   single table, no JOIN, basic WHERE
      - medium: 2-3 tables OR GROUP BY OR nested subquery
      - hard:   4+ tables OR window function OR CTE OR multi-level nesting
    """
    if not sql:
        return DIFFICULTY_EASY
    s = sql.lower()
    tables = extract_referenced_tables(sql)
    n_tables = len(tables)

    has_join = " join " in f" {s} "
    has_group = "group by" in s
    has_subquery = s.count("select") > 1
    has_window = any(kw in s for kw in (" over (", "row_number()", "rank()", "dense_rank()"))
    has_cte = s.strip().startswith("with ")

    if has_window or has_cte or n_tables >= 4:
        return DIFFICULTY_HARD
    if n_tables >= 2 or has_join or has_group or has_subquery:
        return DIFFICULTY_MEDIUM
    return DIFFICULTY_EASY


# --------------------------------------------------------------------------- #
# Per-query verdict
# --------------------------------------------------------------------------- #
@dataclass
class QueryVerdict:
    """One-row evaluation result for a single (question, gold) pair."""
    question: str
    gold_sql: str
    pred_sql: str
    difficulty: str
    # Accuracy signals.
    sql_valid: bool            # did the SQL execute without error?
    ex_correct: Optional[bool] # None = couldn't compare (e.g. gold failed)
    ast_similarity: float
    schema_f1: float
    error_message: str = ""
    # Usability signals (optional - filled by the runner).
    latency_ms: Optional[int] = None
    tokens_used: Optional[int] = None
    # Extended signals (PRD FR-METHOD-02/03).
    schema_f1_columns: Optional[float] = None
    component_overall: Optional[float] = None
    component_select: Optional[float] = None
    component_from: Optional[float] = None
    component_where: Optional[float] = None

    @property
    def passed(self) -> bool:
        """A query counts as 'passed' if either:
          - EX is correct (gold standard), OR
          - AST similarity >= 0.85 AND schema F1 == 1.0
            (catches equivalent rewrites where EX fails on small data)
        """
        if self.ex_correct is True:
            return True
        if self.ex_correct is None:
            return self.ast_similarity >= 0.85 and self.schema_f1 >= 0.95
        return False

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "difficulty": self.difficulty,
            "sql_valid": self.sql_valid,
            "ex_correct": self.ex_correct,
            "ast_similarity": round(self.ast_similarity, 3),
            "schema_f1": round(self.schema_f1, 3),
            "schema_f1_columns": (round(self.schema_f1_columns, 3)
                                  if self.schema_f1_columns is not None else None),
            "component_overall": (round(self.component_overall, 3)
                                  if self.component_overall is not None else None),
            "component_select": (round(self.component_select, 3)
                                 if self.component_select is not None else None),
            "component_from": (round(self.component_from, 3)
                               if self.component_from is not None else None),
            "component_where": (round(self.component_where, 3)
                                if self.component_where is not None else None),
            "passed": self.passed,
            "error_message": self.error_message,
            "latency_ms": self.latency_ms,
            "tokens_used": self.tokens_used,
            "gold_sql": self.gold_sql,
            "pred_sql": self.pred_sql,
        }


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
@dataclass
class EvalReport:
    total: int = 0
    passed: int = 0
    sql_valid: int = 0
    ex_correct: int = 0
    ex_evaluable: int = 0   # how many had a runnable gold
    by_difficulty: dict = field(default_factory=dict)
    avg_ast_similarity: float = 0.0
    avg_schema_f1: float = 0.0
    avg_latency_ms: float = 0.0
    avg_tokens: float = 0.0
    p95_latency_ms: float = 0.0
    failures: list = field(default_factory=list)
    # Extended (PRD FR-METHOD-02/03/07).
    avg_schema_f1_columns: float = 0.0
    avg_component_overall: float = 0.0
    avg_component_select: float = 0.0
    avg_component_where: float = 0.0
    cost_per_correct: Optional[float] = None

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total) if self.total else 0.0

    @property
    def ex_rate(self) -> float:
        """EX rate among queries where EX was actually evaluable."""
        return (self.ex_correct / self.ex_evaluable) if self.ex_evaluable else 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "pass_rate": round(self.pass_rate, 4),
            "sql_valid_rate": round(self.sql_valid / self.total, 4) if self.total else 0.0,
            "ex_rate": round(self.ex_rate, 4),
            "ex_evaluable": self.ex_evaluable,
            "by_difficulty": self.by_difficulty,
            "avg_ast_similarity": round(self.avg_ast_similarity, 4),
            "avg_schema_f1": round(self.avg_schema_f1, 4),
            "avg_schema_f1_columns": round(self.avg_schema_f1_columns, 4),
            "avg_component_overall": round(self.avg_component_overall, 4),
            "avg_component_select": round(self.avg_component_select, 4),
            "avg_component_where": round(self.avg_component_where, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "avg_tokens": round(self.avg_tokens, 2),
            "cost_per_correct": (round(self.cost_per_correct, 6)
                                 if self.cost_per_correct is not None else None),
            "failures_count": len(self.failures),
        }


def aggregate_verdicts(
    verdicts: list[QueryVerdict],
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    price_in_per_1k: float = 0.0,
    price_out_per_1k: float = 0.0,
) -> EvalReport:
    """Roll per-query verdicts up into a summary report.

    Optional token + price inputs enable cost-efficiency (FR-METHOD-07).
    If `tokens_in/out` > 0 and `price_*` > 0 and at least one query
    passed, `cost_per_correct` is populated.
    """
    rep = EvalReport(total=len(verdicts))
    if not verdicts:
        return rep

    ast_sum = f1_sum = lat_sum = tok_sum = 0.0
    latencies = []
    by_diff: dict[str, dict] = {}
    col_f1_sum = 0.0
    col_f1_n = 0
    comp_overall_sum = 0.0
    comp_select_sum = 0.0
    comp_where_sum = 0.0
    comp_n = 0

    for v in verdicts:
        rep.sql_valid += int(v.sql_valid)
        if v.ex_correct is not None:
            rep.ex_evaluable += 1
            rep.ex_correct += int(v.ex_correct is True)
        if v.passed:
            rep.passed += 1
        ast_sum += v.ast_similarity
        f1_sum += v.schema_f1
        if v.latency_ms is not None:
            lat_sum += v.latency_ms
            latencies.append(v.latency_ms)
        if v.tokens_used is not None:
            tok_sum += v.tokens_used
        if v.schema_f1_columns is not None:
            col_f1_sum += v.schema_f1_columns
            col_f1_n += 1
        if v.component_overall is not None:
            comp_overall_sum += v.component_overall
            comp_n += 1
        if v.component_select is not None:
            comp_select_sum += v.component_select
        if v.component_where is not None:
            comp_where_sum += v.component_where
        if not v.passed:
            rep.failures.append(v.to_dict())

        d = v.difficulty
        if d not in by_diff:
            by_diff[d] = {"total": 0, "passed": 0}
        by_diff[d]["total"] += 1
        by_diff[d]["passed"] += int(v.passed)

    # Per-difficulty pass rate.
    for d, s in by_diff.items():
        s["pass_rate"] = round(s["passed"] / s["total"], 4) if s["total"] else 0.0

    rep.by_difficulty = by_diff
    rep.avg_ast_similarity = ast_sum / rep.total
    rep.avg_schema_f1 = f1_sum / rep.total
    rep.avg_schema_f1_columns = (col_f1_sum / col_f1_n) if col_f1_n else 0.0
    rep.avg_component_overall = (comp_overall_sum / comp_n) if comp_n else 0.0
    rep.avg_component_select = (comp_select_sum / comp_n) if comp_n else 0.0
    rep.avg_component_where = (comp_where_sum / comp_n) if comp_n else 0.0

    if latencies:
        rep.avg_latency_ms = lat_sum / len(latencies)
        latencies.sort()
        # P95 = index ceil(0.95 * N) - 1
        idx = max(0, min(len(latencies) - 1, int(0.95 * len(latencies))))
        rep.p95_latency_ms = latencies[idx]

    if any(v.tokens_used is not None for v in verdicts):
        n_tok = sum(1 for v in verdicts if v.tokens_used is not None)
        rep.avg_tokens = tok_sum / n_tok if n_tok else 0.0

    # Cost efficiency: prefer explicit totals, else fall back to per-query sum.
    if tokens_in > 0 or tokens_out > 0:
        rep.cost_per_correct = cost_efficiency(
            rep.passed, tokens_in, tokens_out,
            price_in_per_1k, price_out_per_1k,
        )
    elif price_in_per_1k > 0 or price_out_per_1k > 0:
        # Sum per-query tokens as fallback.
        ti = sum(v.tokens_used or 0 for v in verdicts)
        rep.cost_per_correct = cost_efficiency(
            rep.passed, ti, 0, price_in_per_1k, price_out_per_1k,
        )

    return rep
