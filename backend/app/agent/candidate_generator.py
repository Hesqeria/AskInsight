"""Multi-candidate plan generator for the clarification workflow.

When a question is ambiguous, instead of asking the user to fill in a
single missing field, we generate a list of *complete* SemanticPlan
candidates covering the most plausible interpretations, each with a
human-readable explanation + SQL preview. The user picks one (or the
reranker orders them by relevance first).

Design (PRD: 模糊提问澄清交互 + 多路重排)
-----------------------------------------
- Measure dimension: for each plausible business term in
  MEASURE_REGISTRY that could match the question, build a plan.
- Time dimension: if the user gave no time, attach the most common
  default windows (本月 / 上月 / 近7天) as separate candidates.
- Dimension dimension: if the question mentions a region-like token,
  attach group_by=地区 as a variant.

Output: list[dict] where each dict is a SemanticPlan.to_dict() with
`_candidate_id` and `_explain` added.
"""
from __future__ import annotations

import itertools
import uuid
from datetime import date
from typing import Optional

from app.agent.nodes.semantic_grounding import (
    MEASURE_REGISTRY,
    JOIN_REGISTRY,
    DIM_VALUE_COLUMN_REGISTRY,
    match_business_term,
    parse_time_expression,
    _BUSINESS_TERM_ALIASES,
)
from app.ontology.plan import (
    SemanticPlan, Measure, DimensionFilter, DimensionGroupBy,
    TimeRange, JoinSpec, render_sql_from_plan,
)
from app.core.log import logger


# Default time windows to propose when the user gave no time expression.
_DEFAULT_TIME_WINDOWS = ["本月", "上月", "近7天"]

# Default group_by variants to propose for ambiguous questions.
_DEFAULT_GROUP_BYS = ["C050", "C021"]  # 按地区 / 按品类


def generate_candidates(
    question: str,
    *,
    keywords: Optional[list[str]] = None,
    matched_dimension_values: Optional[list[dict]] = None,
    glossary_matches: Optional[list[dict]] = None,
    today: Optional[date] = None,
    max_candidates: int = 4,
) -> list[dict]:
    """Generate a ranked list of complete SemanticPlan candidates.

    Returns a list of plan dicts (each with `_candidate_id` +
    `_explain` + `sql_preview` + `confidence`). The caller (reranker /
    ask_clarification) orders and presents them.

    Strategy:
      1. Determine the measure candidates: if `match_business_term`
         matched exactly, keep just that one (plus maybe alternates);
         otherwise, take the top-N most plausible registry terms.
      2. Determine time: use user's explicit time if present; else
         attach the top-2 default windows.
      3. Optionally attach a group_by variant if a dim token appeared.

    Combination explosion is capped by `max_candidates` (best-effort:
    prefer the most-confident combinations first).
    """
    if not question:
        return []

    # --- 1. Measure candidates ---------------------------------------
    text_for_match = question + " " + " ".join(keywords or [])
    if glossary_matches:
        for gm in glossary_matches:
            text_for_match += " " + (gm.get("term") or "")

    exact = match_business_term(text_for_match)
    measure_terms: list[str]
    if exact:
        # User wording hit a term. Still offer 1-2 alternates so they
        # can switch interpretation in one click.
        measure_terms = [exact]
        for t in _prioritized_terms():
            if t not in measure_terms:
                measure_terms.append(t)
    else:
        # No match -> offer the most common terms as alternatives.
        measure_terms = _prioritized_terms()

    # --- 2. Time candidates ------------------------------------------
    explicit_time = parse_time_expression(question, today=today)
    if explicit_time:
        time_options: list[Optional[tuple]] = [explicit_time]
    else:
        time_options = [
            parse_time_expression(w, today=today)
            for w in _DEFAULT_TIME_WINDOWS
            if parse_time_expression(w, today=today)
        ][:2]
        # Also include "no time" as a candidate (full-range default).
        if len(time_options) < 2:
            time_options.append(None)

    # --- 3. Dimension / group_by variants ----------------------------
    dim_matches = _dimension_matches(matched_dimension_values)
    group_variants: list[Optional[list[str]]] = [None]  # no grouping
    if dim_matches:
        # The question already has a region-like filter; offer grouping too.
        group_variants.append(["C050"])
    else:
        # Ambiguous -> offer the default group-bys as alternatives.
        group_variants.append(_DEFAULT_GROUP_BYS)
    # --- Assemble combinations, capped by max_candidates -------------
    candidates: list[dict] = []
    seen_sql: set[str] = set()
    for measure_term in measure_terms:
        if len(candidates) >= max_candidates:
            break
        for time_opt in time_options:
            for gb in group_variants:
                if len(candidates) >= max_candidates:
                    break
                plan = _build_single_candidate(
                    question=question,
                    measure_term=measure_term,
                    time_opt=time_opt,
                    group_by_classes=gb,
                    dim_matches=dim_matches,
                    today=today,
                )
                if plan is None:
                    continue
                try:
                    sql = render_sql_from_plan(plan)
                except Exception:
                    continue
                sql_norm = " ".join(sql.split())
                if sql_norm in seen_sql:
                    continue
                seen_sql.add(sql_norm)

                cid = f"cand_{uuid.uuid4().hex[:8]}"
                d = plan.to_dict()
                d["_candidate_id"] = cid
                d["_explain"] = _explain_candidate(plan)
                d["sql_preview"] = sql
                candidates.append(d)

    return candidates


def _prioritized_terms() -> list[str]:
    """Return registry terms ordered by business commonness (GMV first)."""
    order = ["GMV", "order_count", "DAU"]
    rest = [t for t in MEASURE_REGISTRY if t not in order]
    return order + rest


def _dimension_matches(matched_values) -> list[tuple[str, str, str]]:
    """Reuse the grounding node's dimension-value matcher."""
    if not matched_values:
        return []
    from app.agent.nodes.semantic_grounding import match_dimension_class
    return match_dimension_class(matched_values)


def _build_single_candidate(
    *,
    question: str,
    measure_term: str,
    time_opt: Optional[tuple],
    group_by_classes: Optional[list[str]],
    dim_matches: list[tuple[str, str, str]],
    today: Optional[date],
) -> Optional[SemanticPlan]:
    """Assemble one complete plan for a (measure, time, group) combo."""
    spec = MEASURE_REGISTRY.get(measure_term)
    if not spec:
        return None

    plan = SemanticPlan(question=question)
    plan.measures.append(Measure(
        business_term=measure_term,
        class_id=spec["class_id"],
        column=spec["column"],
        aggregation=spec["aggregation"],
        pre_filters=list(spec.get("pre_filters", [])),
    ))

    # Time
    if time_opt:
        start, end = time_opt
        plan.time = TimeRange(column="t.dt", start=start.isoformat(),
                              end=end.isoformat())

    # Dimensions (region-like filter)
    for cid, col, val in dim_matches:
        plan.dimensions.append(DimensionFilter(
            class_id=cid, column=col, operator="=", value=val,
        ))
        _add_join_for(plan, cid)

    # Group-by
    measure_class = plan.measures[0].class_id if plan.measures else ""
    for cid in (group_by_classes or []):
        col_info = DIM_VALUE_COLUMN_REGISTRY.get(cid)
        if not col_info:
            continue
        # Skip dimension classes we have no JOIN path for (prevents
        # dangling-alias SQL like DAU grouped by category).
        if (measure_class, cid) not in JOIN_REGISTRY:
            continue
        plan.group_by.append(DimensionGroupBy(
            class_id=cid, column=col_info["column"],
        ))
        _add_join_for(plan, cid)

    # Confidence: measure is explicit, time set? dims optional.
    plan.confidence = _candidate_confidence(plan)
    plan.grounding_source = "candidate"
    return plan


def _add_join_for(plan: SemanticPlan, dim_class_id: str) -> None:
    """Add the JOIN path for a dimension class if not already present."""
    if not plan.measures:
        return
    key = (plan.measures[0].class_id, dim_class_id)
    if key not in JOIN_REGISTRY:
        return
    expected_table = DIM_VALUE_COLUMN_REGISTRY.get(dim_class_id, {}).get("table", "")
    has_join = bool(expected_table) and any(
        expected_table.split(".")[-1] in j.right_table_real
        for j in plan.joins
    )
    if not has_join:
        for j in JOIN_REGISTRY[key]:
            plan.joins.append(JoinSpec(**j))


def _candidate_confidence(plan: SemanticPlan) -> float:
    """Candidate confidence: measure + time => 0.9; + dims => 1.0."""
    if not plan.measures:
        return 0.3
    has_time = plan.time is not None
    has_dim_or_group = bool(plan.dimensions or plan.group_by)
    if has_time and has_dim_or_group:
        return 1.0
    if has_time:
        return 0.9
    if has_dim_or_group:
        return 0.8
    return 0.7


def _explain_candidate(plan: SemanticPlan) -> str:
    """Human-readable one-liner for the candidate (reuses explain layer)."""
    try:
        from app.ontology.explain import to_business_summary
        return to_business_summary(plan)
    except Exception:
        m = plan.measures[0] if plan.measures else None
        base = f"{m.business_term} ({m.aggregation})" if m else "?"
        if plan.time:
            base += f" {plan.time.start}~{plan.time.end}"
        if plan.group_by:
            base += f" 按{len(plan.group_by)}个维度分组"
        return base
