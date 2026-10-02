"""Semantic Plan: ontology-grounded intermediate representation (IR).

Sits between NL question and final SQL. Lets us:
  1. Ground NL in ontology (measure class, dimension class, joins)
  2. Validate the plan before generating SQL
  3. Render SQL deterministically from a template (no LLM call needed
     for well-structured queries)
  4. Explain to users *why* this SQL was generated

The plan is pure data (no I/O) so it's trivially testable + cacheable.

Reference: 本体建模语义中间表示 - converts NL like
    "华北 上个月的gmv"
into a plan that renders to:
    SELECT SUM(total_amount)
    FROM dw.dwd_order_info_inc oi
    JOIN dw.dim_base_province p ON oi.province_id = p.id
    JOIN dw.dim_base_region r ON p.region_id = r.id
    WHERE oi.order_status = 'PAID'
      AND r.region_name = '华北'
      AND oi.dt BETWEEN '2026-07-01' AND '2026-07-31'
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# --------------------------------------------------------------------------- #
# Plan primitives
# --------------------------------------------------------------------------- #
@dataclass
class Measure:
    """A measure = aggregable column on a fact table.

    `pre_filters` captures business semantics like "不含退款" which
    lives in lineage_business (e.g. order_status='PAID').
    """
    business_term: str           # "GMV"
    class_id: str                # "C030" (订单)
    column: str                  # "dw.dwd_order_info_inc.total_amount"
    aggregation: str             # "SUM" | "COUNT" | "AVG" | "MAX" | "MIN" | "COUNT_DISTINCT"
    pre_filters: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DimensionFilter:
    """A dimension value filter (the WHERE on a dimension column).

    Example: region_name = '华北'
    """
    class_id: str                # "C050" (地域)
    column: str                  # "dw.dim_base_region.region_name"
    operator: str = "="          # "=" | "!=" | "IN" | "LIKE" | ">" | "<"
    value: object = None         # str | list[str] | number

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DimensionGroupBy:
    """GROUP BY on a dimension column (for breakdown queries).

    Example: 'GROUP BY region_name' for "各地区的GMV"
    """
    class_id: str
    column: str
    # entity key columns (u.id) group without being selected, so
    # same-name entities stay separate rows (utopia #221 adoption)
    select: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TimeRange:
    """A date-range filter applied to the fact table's date column.

    `column` is the dt field on the fact table, NOT dim_date — we keep
    things simple and let Doris's partition pruning handle it.
    """
    column: str                  # "dw.dwd_order_info_inc.dt"
    start: str                   # "2026-07-01" (inclusive)
    end: str                     # "2026-07-31" (inclusive)
    grain: str = "day"           # "day" | "month" — for further grouping

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JoinSpec:
    """One JOIN clause derived from ont_relation.

    For Order→Province→Region, two JoinSpecs are produced:
      JoinSpec(fact_table='oi', left='province_id', dim_table='p', right='id')
      JoinSpec(fact_table='p',   left='region_id',  dim_table='r', right='id')
    """
    left_table: str              # alias of left table in FROM/JOIN chain
    left_column: str             # FK column on left table
    right_table: str             # alias of right table
    right_column: str            # PK column on right table
    right_table_real: str        # full table name e.g. "dw.dim_base_region"
    join_type: str = "INNER"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrderBy:
    """ORDER BY clause primitive (OPT-M7 ranking/Top-N)."""
    column: str
    direction: str = "DESC"

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #
@dataclass
class SemanticPlan:
    """Ontology-grounded IR for one NL question.

    A well-formed plan has ≥1 measure. Dimensions/group_by/time/joins
    are optional. `confidence` < 1.0 means grounding was partial
    (e.g. ambiguous term, missing join in ontology) and downstream
    nodes may want to fall back to LLM-based generate_sql.
    """
    question: str
    measures: list[Measure] = field(default_factory=list)
    dimensions: list[DimensionFilter] = field(default_factory=list)
    group_by: list[DimensionGroupBy] = field(default_factory=list)
    time: Optional[TimeRange] = None
    joins: list[JoinSpec] = field(default_factory=list)
    extra_filters: list[str] = field(default_factory=list)
    # OPT-M7: 排名/Top-N/最高最低
    order_by: list[OrderBy] = field(default_factory=list)
    limit: Optional[int] = None
    having: list[str] = field(default_factory=list)
    grounding_source: str = "rule"  # "rule" | "llm" | "hybrid"
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)
    # PRD: period-over-period (环比/同比) needs window functions -> the plan
    # must never take the deterministic render path; LLM generates the SQL.
    pop: bool = False
    # Dual-period compare windows [(label, start, end), ...] - when set
    # (and the plan is join/dim free) the renderer emits a UNION ALL SQL
    # deterministically instead of delegating to the LLM.
    pop_windows: list = field(default_factory=list)
    # Fan-out fix: when the measure aggregates a JOINED table's column
    # (e.g. detail.split_total_amount over the info fact), the FROM table
    # must stay the fact - not derived from the measure column.
    fact_table_override: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "SemanticPlan":
        """Reconstruct a plan from its dict form (state/checkpoint
        round-trips pass dicts). Single source of truth - three
        near-identical private copies existed in generate_sql /
        merge_clarification / ask_clarification, two of which silently
        dropped order_by/limit/having (ranking lost on clarify resume).
        """
        return cls(
            question=d.get("question", ""),
            measures=[Measure(**m) for m in d.get("measures", [])],
            dimensions=[DimensionFilter(**x) for x in d.get("dimensions", [])],
            group_by=[DimensionGroupBy(**x) for x in d.get("group_by", [])],
            time=TimeRange(**d["time"]) if d.get("time") else None,
            joins=[JoinSpec(**x) for x in d.get("joins", [])],
            extra_filters=d.get("extra_filters", []),
            order_by=[OrderBy(**o) for o in d.get("order_by", [])],
            limit=d.get("limit"),
            having=d.get("having", []),
            grounding_source=d.get("grounding_source", "rule"),
            confidence=d.get("confidence", 1.0),
            notes=d.get("notes", []),
            pop=bool(d.get("pop", False)),
            pop_windows=[tuple(w) for w in (d.get("pop_windows") or [])],
            fact_table_override=d.get("fact_table_override", ""),
        )

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    @property
    def is_valid(self) -> bool:
        """A plan is structurally valid iff it has ≥1 measure and a
        resolvable fact table."""
        return len(self.measures) >= 1 and "." in self.measures[0].column

    @property
    def fact_table(self) -> str:
        """Fully-qualified fact table from the first measure's column.

        Example: column 'dw.dwd_order_info_inc.total_amount' → 'dw.dwd_order_info_inc'
        """
        if not self.measures:
            return ""
        col = self.measures[0].column
        # Strip column part: "dw.table.col" → "dw.table"
        parts = col.split(".")
        if len(parts) >= 2:
            return ".".join(parts[:2])
        return col

    @property
    def fact_table_short(self) -> str:
        """Short table name (without schema) for aliasing.

        Example: 'dw.dwd_order_info_inc' → 'dwd_order_info_inc'
        """
        return self.fact_table.split(".")[-1]

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "measures": [m.to_dict() for m in self.measures],
            "dimensions": [d.to_dict() for d in self.dimensions],
            "group_by": [g.to_dict() for g in self.group_by],
            "time": self.time.to_dict() if self.time else None,
            "joins": [j.to_dict() for j in self.joins],
            "extra_filters": self.extra_filters,
            "order_by": [o.to_dict() for o in self.order_by],
            "limit": self.limit,
            "having": self.having,
            "grounding_source": self.grounding_source,
            "confidence": round(self.confidence, 3),
            "notes": self.notes,
            "pop": self.pop,
            "pop_windows": [list(w) for w in (self.pop_windows or [])],
            "fact_table_override": self.fact_table_override,
        }


# --------------------------------------------------------------------------- #
# SQL renderer (pure template, no LLM)
# --------------------------------------------------------------------------- #
def _quote_value(value: object) -> str:
    """Render a Python value as SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "(" + ", ".join(_quote_value(v) for v in value) + ")"
    # String: single-quote, escape embedded quotes.
    s = str(value).replace("'", "''")
    return f"'{s}'"


_METRIC_LABELS = {
    "cashier_anomaly_count": "收银异常数",
    "cart_item_count": "加购件数", "cart_user_count": "加购人数",
    "favor_count": "收藏数", "good_rate": "好评率(%)",
    "order_count": "订单数", "payer_count": "支付用户数",
    "avg_order_amount": "客单价", "DAU": "日活",
    "repurchase_rate": "复购率", "retention_rate": "留存率",
}


def render_sql_from_plan(plan: SemanticPlan) -> str:
    """Deterministically render a SemanticPlan into a Doris-compatible SQL.

    This is the "happy path" SQL generator. For plans that LLM should
    still handle (e.g. complex subqueries, window functions), set
    `plan.confidence < 0.7` and the agent will fall back to LLM.

    Output format:
        SELECT <agg>(<col>) [, ...]
        FROM <fact_table> [AS alias]
        [JOIN <dim_table> [AS alias] ON ...]
        [WHERE <pre_filter> [AND <dim_filter>] [AND dt BETWEEN ...]]

    Column rewriting: measures/dimensions often store fully-qualified
    columns like 'dw.dwd_order_info_inc.total_amount'. We rewrite those
    to alias form ('t.total_amount') because Doris errors out when the
    FROM table has an alias but the SELECT column uses the real name.
    """
    if not plan.is_valid:
        raise ValueError(f"Cannot render SQL from invalid plan: {plan.to_dict()}")

    # Dual-period compare: deterministic UNION ALL (no LLM needed).
    if getattr(plan, "pop_windows", None):
        return _render_pop_union(plan)

    fact_table = plan.fact_table_override or plan.fact_table
    fact_alias = "t"  # main fact table gets alias 't'
    fact_table_short = fact_table.split(".")[-1]

    def _qualify(col: str) -> str:
        """Rewrite 'dw.<fact_table_short>.<col>' → 't.<col>'.

        Leaves other qualified names alone (e.g. dimension columns
        that already use join aliases like 'r.region_name').
        """
        if not col:
            return col
        prefix = f"dw.{fact_table_short}."
        if col.startswith(prefix):
            return f"{fact_alias}.{col[len(prefix):]}"
        return col

    # ----- SELECT -----
    # Business-readable aliases: the frontend shows column names verbatim,
    # so aggregates get the metric name ("GMV") and group columns get the
    # dimension label ("区域") instead of raw SQL like SUM(t.gmv)/r.region_name.
    _DIM_LABELS = {"C050": "区域", "C021": "品类", "C011": "用户",
               "CSA": "服务区", "CC": "县市", "C050P": "省份", "C080": "日期"}
    _METRIC_LABELS = {
        "order_count": "订单数", "payer_count": "支付用户数",
        "avg_order_amount": "客单价", "DAU": "日活",
        "repurchase_rate": "复购率", "retention_rate": "留存率",
    }
    select_parts = []
    for m in plan.measures:
        col = _qualify(m.column)
        if m.aggregation.upper() == "GOOD_RATE":
            expr = (
                f"ROUND(SUM({col})"
                f" / NULLIF(SUM(t.comment_count), 0) * 100, 2)"
            )
        elif m.aggregation.upper() == "COUNT_DISTINCT":
            expr = f"COUNT(DISTINCT {col})"
        else:
            expr = f"{m.aggregation}({col})"
        alias = (m.business_term or "").replace('"', "")
        alias = _METRIC_LABELS.get(alias, alias)
        select_parts.append(f'{expr} AS "{alias}"' if alias else expr)
    # Add group-by columns if present (so breakdown queries show labels).
    for g in plan.group_by:
        if not g.select:
            continue
        gcol = _qualify(g.column)
        label = _DIM_LABELS.get(g.class_id, "").replace('"', "")
        select_parts.append(f'{gcol} AS "{label}"' if label else gcol)
    select_clause = "SELECT " + ", ".join(select_parts)

    # ----- FROM -----
    from_clause = f"FROM {fact_table} AS {fact_alias}"

    # ----- JOINs -----
    join_clauses = []
    for j in plan.joins:
        join_clauses.append(
            f"{j.join_type} JOIN {j.right_table_real} AS {j.right_table} "
            f"ON {j.left_table}.{j.left_column} = {j.right_table}.{j.right_column}"
        )

    # ----- WHERE -----
    where_parts: list[str] = []

    # Pre-filters from measure (e.g. order_status='PAID')
    for m in plan.measures:
        for pf in m.pre_filters:
            # Substitute unqualified columns with fact_table alias.
            # Simple heuristic: if the filter has no '.', qualify it.
            if "." not in pf:
                pf = f"{fact_alias}.{pf}"
            where_parts.append(pf)

    # Dimension filters (already aliased in DIM_VALUE_COLUMN_REGISTRY).
    for d in plan.dimensions:
        col = d.column
        op = d.operator.upper()
        if op in ("IN", "NOT IN"):
            val = _quote_value(d.value if isinstance(d.value, list) else [d.value])
            where_parts.append(f"{col} {op} {val}")
        elif op == "IS NULL":
            where_parts.append(f"{col} IS NULL")
        elif op == "IS NOT NULL":
            where_parts.append(f"{col} IS NOT NULL")
        else:
            where_parts.append(f"{col} {op} {_quote_value(d.value)}")

    # Time range
    if plan.time:
        where_parts.append(
            f"{plan.time.column} BETWEEN '{plan.time.start}' AND '{plan.time.end}'"
        )

    # Extra filters (free-form strings, used as-is)
    where_parts.extend(plan.extra_filters)

    where_clause = ""
    if where_parts:
        where_clause = "WHERE " + " AND ".join(where_parts)

    # ----- GROUP BY -----
    group_by_clause = ""
    if plan.group_by:
        group_by_clause = "GROUP BY " + ", ".join(_qualify(g.column) for g in plan.group_by)

    # ----- HAVING (OPT-M7) -----
    having_clause = ""
    if plan.having:
        having_clause = "HAVING " + " AND ".join(plan.having)

    # ----- ORDER BY (OPT-M7) -----
    order_by_clause = ""
    if plan.order_by:
        parts = []
        for o in plan.order_by:
            col = o.column
            parts.append(f"{col} {o.direction.upper()}")
        order_by_clause = "ORDER BY " + ", ".join(parts)

    # ----- LIMIT (OPT-M7) -----
    limit_clause = ""
    if plan.limit and plan.limit > 0:
        limit_clause = f"LIMIT {int(plan.limit)}"

    # ----- Assemble -----
    sql = "\n".join(p for p in [
        select_clause, from_clause, *join_clauses,
        where_clause, group_by_clause, having_clause,
        order_by_clause, limit_clause,
    ] if p)
    return sql

def _render_pop_union(plan) -> str:
    """SELECT '<label>' AS 周期, <agg> ... UNION ALL for pop_windows."""
    m = plan.measures[0]
    col = m.column.split(".")[-1]
    if m.aggregation.upper() == "GOOD_RATE":
        expr = ("ROUND(SUM(t.good_comment_count)"
                " / NULLIF(SUM(t.comment_count), 0) * 100, 2)")
    elif m.aggregation.upper() == "COUNT_DISTINCT":
        expr = f"COUNT(DISTINCT t.{col})"
    else:
        expr = f"{m.aggregation}(t.{col})"
    alias_m = _METRIC_LABELS.get(m.business_term, m.business_term)
    fact_table = plan.fact_table_override or plan.fact_table
    pre = ""
    if m.pre_filters:
        pre = " AND ".join(
            pf if "." in pf else f"t.{pf}" for pf in m.pre_filters)
    parts = []
    for label, w_start, w_end in plan.pop_windows:
        where = f"t.dt BETWEEN '{w_start}' AND '{w_end}'"
        if pre:
            where = f"{pre} AND {where}"
        parts.append(
            f"SELECT '{label}' AS 周期, {expr} AS \"{alias_m}\" "
            f"FROM {fact_table} AS t WHERE {where}")
    return ("\nUNION ALL\n".join(parts))
