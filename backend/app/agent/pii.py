"""PII policy gate for SQL (Phase 4 PRD §6.2 + agent integration).

When a generated or corrected SQL references a column that maps to a
high-PII ontology class (per `ont_class.pii_level` with ancestor-chain
inheritance), the agent should flag it for human approval rather than
auto-execute.

Two parts:
  1. `extract_referenced_columns(sql)` - regex-based extraction of
     `<alias>.<column>` / `table.column` references from a SQL string.
     Intentionally lightweight (no full SQL parser) since the ontology
     lookup itself is the authoritative filter.
  2. `check_pii_violations(sql, ontology_repo, threshold)` - looks up
     each extracted column in `ont_instance`, computes the effective
     PII level of its class (with inheritance via `OntologyGraph`),
     and returns any column whose level >= threshold.

The function is safe to call when ontology tables don't exist yet
(repo methods return empty), in which case it reports no violations.
"""
import re
from dataclasses import dataclass
from typing import Iterable, Optional


# Default PII level that requires human approval. PRD §6.2:
#   0 = no PII, 1 = low, 2 = medium, 3 = high (user-identifying)
DEFAULT_PII_APPROVAL_THRESHOLD = 3


@dataclass(frozen=True)
class PiiViolation:
    """One column in the SQL whose effective PII level breaches the gate."""
    column_ref: str           # e.g. "dw.dim_user_info.phone_num"
    class_id: str             # e.g. "C011"
    class_name: str
    effective_pii_level: int
    own_pii_level: int        # the column's class, before inheritance


# ------------------------------------------------------------------ #
# SQL column extraction
# ------------------------------------------------------------------ #
# Match `alias.column` or `table.column` (no schema prefix - we resolve
# via the table_alias_map below). Quoted variants ``t``.``c`` also work.
_COLUMN_REF_RE = re.compile(
    r'(?<![A-Za-z0-9_.])'                          # left boundary
    r'(?P<table>`?[A-Za-z_][A-Za-z0-9_]*`?)'
    r'\.'
    r'(?P<col>`?[A-Za-z_][A-Za-z0-9_]*`?)'
    r'(?![A-Za-z0-9_])'                            # right boundary
)

# Match `FROM/JOIN <schema>.<table> [AS] <alias>` to resolve aliases.
# Tolerates schema-qualified names (dw.table) which the legacy lineage
# regex in extract_lineage.py does not.
_TABLE_DECL_RE = re.compile(
    r'(?:FROM|JOIN)\s+'
    r'(?P<schema>`?[A-Za-z_][A-Za-z0-9_]*`?)'
    r'(?:\s*\.\s*(?P<table>`?[A-Za-z_][A-Za-z0-9_]*`?))?'
    r'(?:\s+(?:AS\s+)?(?P<alias>`?[A-Za-z_][A-Za-z0-9_]*`?))?'
    r'(?=\s|,|$)',
    re.IGNORECASE,
)

# Reserved words we don't treat as table identifiers.
_SQL_KEYWORDS = {
    "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "SELECT", "FROM",
    "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "ON", "AND", "OR",
    "NOT", "IN", "BETWEEN", "LIKE", "IS", "NULL", "AS", "BY",
    "ASC", "DESC", "UNION", "ALL", "DISTINCT", "CASE", "WHEN", "THEN",
    "ELSE", "END", "WITH", "OVER", "PARTITION",
}


def _strip_backticks(s: str) -> str:
    return s.strip().strip("`")


def extract_referenced_columns(sql: str) -> list[tuple[str, str]]:
    """Return a list of `(table_or_alias, column)` tuples referenced in
    the SQL. Aliases are resolved to their declared table names where
    possible (else the alias name is returned as-is).

    Best-effort - false positives are fine because the ontology lookup
    filters out anything that isn't a known PII column. The goal is to
    not miss real PII references."""
    if not sql:
        return []

    # 1. Build alias -> table map from FROM/JOIN clauses.
    alias_to_table: dict[str, str] = {}
    for m in _TABLE_DECL_RE.finditer(sql):
        table_part = m.group("table") or m.group("schema")
        alias_part = m.group("alias")
        if not table_part:
            continue
        table_name = _strip_backticks(table_part)
        if table_name.upper() in _SQL_KEYWORDS:
            continue
        # Register both the alias and the table name themselves.
        if alias_part:
            alias_name = _strip_backticks(alias_part)
            if alias_name.upper() not in _SQL_KEYWORDS:
                alias_to_table[alias_name.lower()] = table_name
        alias_to_table[table_name.lower()] = table_name

    # 2. Find all `t.c` references and resolve.
    seen = set()
    out = []
    for m in _COLUMN_REF_RE.finditer(sql):
        t = _strip_backticks(m.group("table"))
        c = _strip_backticks(m.group("col"))
        if t.upper() in _SQL_KEYWORDS or c.upper() in _SQL_KEYWORDS:
            continue
        resolved = alias_to_table.get(t.lower(), t)
        key = (resolved, c)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


# ------------------------------------------------------------------ #
# PII check
# ------------------------------------------------------------------ #
async def check_pii_violations(
    sql: str,
    ontology_repo,
    threshold: int = DEFAULT_PII_APPROVAL_THRESHOLD,
    default_db: str = "dw",
) -> list[PiiViolation]:
    """Scan `sql` for PII columns at or above `threshold`.

    `ontology_repo` is an `OntologyRepository`. When the ontology
    tables are empty/missing, returns [] - so this is safe to call
    unconditionally in the agent pipeline.

    Resolution order for each (table, column) reference:
      1. Try `dw.<table>.<column>` (default schema)
      2. Try `<table>.<column>` (already qualified)
      3. Give up (no ontology match -> not a PII column)

    The effective PII level walks up `parent_class_id` so e.g. a
    MemberLevel column inherits its User parent's pii_level=3.
    """
    if not sql:
        return []

    refs = extract_referenced_columns(sql)

    # Load the full ontology graph once for the inheritance walk.
    from app.ontology import OntologyGraph
    try:
        classes = await ontology_repo.list_classes()
        relations = await ontology_repo.list_relations()
        instances = await ontology_repo.list_instances()
    except Exception:
        return []
    if not classes or not instances:
        return []

    # Bare-column fallback: the regex extractor only matches `t.c`
    # pairs, so `SELECT phone_num FROM dim_user_info` extracts nothing
    # and used to bypass the gate entirely. For every ontology
    # instance, when BOTH its column name (word-boundary) and its table
    # name appear in the SQL, add the (table, column) ref. Conservative
    # by design (fail-closed): same-name columns from unrelated tables
    # can only cause an extra approval, never a miss.
    import re as _re
    sql_l = sql.lower()
    _seen_ref = {(t.lower(), c.lower()) for t, c in refs}
    for inst in instances:
        parts = inst.instance_id.split(".")
        if len(parts) < 3:
            continue
        table, column = parts[-2], parts[-1]
        if (table, column) in _seen_ref:
            continue
        if table.lower() not in sql_l:
            continue
        if _re.search(r"(?<![\w.])" + _re.escape(column)
                      + r"(?![\w])", sql, _re.IGNORECASE):
            refs.append((table, column))
            _seen_ref.add((table, column))
    if not refs:
        return []

    graph = OntologyGraph(classes, relations)
    instance_by_id = {inst.instance_id: inst for inst in instances}

    violations: list[PiiViolation] = []
    seen_classes: set[str] = set()
    for table, column in refs:
        # Try several instance_id shapes; the PRD uses db.table.column.
        candidates = [
            f"{default_db}.{table}.{column}",
            f"{table}.{column}",
        ]
        inst = None
        for c in candidates:
            inst = instance_by_id.get(c)
            if inst:
                break
        if inst is None:
            continue

        cls = graph.classes.get(inst.class_id)
        if cls is None:
            continue
        own_pii = cls.pii_level
        effective = graph.effective_pii_level(inst.class_id)
        if effective < threshold:
            continue
        # De-dup by class - we only need one violation per ontology class.
        if inst.class_id in seen_classes:
            continue
        seen_classes.add(inst.class_id)
        violations.append(PiiViolation(
            column_ref=f"{default_db}.{table}.{column}",
            class_id=inst.class_id,
            class_name=cls.class_name,
            effective_pii_level=effective,
            own_pii_level=own_pii,
        ))
    return violations
