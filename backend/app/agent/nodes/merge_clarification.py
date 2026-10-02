"""merge_clarification node: merge the user's clarification response
into the SemanticPlan, re-evaluate confidence, and route.

This node runs on the RESUMED graph (after the user POSTs their
response to `/api/v1/clarify/{id}/resume`). It:

  1. **Loads the pending clarify_session** from the DB (carried via
     state on resume — see `clarify_router.py`).

  2. **Merges user selections/free_text** into the initial plan:
       - measure   → set measures[0] from MEASURE_REGISTRY
       - time      → parse via `parse_time_expression`
       - group_by  → add DimensionGroupBy + JOIN entries
       - dimension → add DimensionFilter + JOIN entries

  3. **Re-evaluates confidence** — typically bumps 0.5 → 1.0 if the
     user filled all missing fields.

  4. **Updates state.semantic_plan** so downstream `generate_sql` can
     use the merged plan (short-circuiting the LLM path when
     confidence is high enough).

After this node runs, the resumed graph continues to `generate_sql`.
If the user submitted more amendments (status="amended"), the router
can decide to re-route back to ask_clarification for another round.

Pure helper functions (`apply_clarification_to_plan`, time/measure
mergers) are exported so unit tests can verify the merge logic without
DB or LLM round-trips.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.nodes.semantic_grounding import (
    adapt_measure_source,
    MEASURE_REGISTRY, JOIN_REGISTRY, DIM_VALUE_COLUMN_REGISTRY,
    parse_time_expression,
)
from app.core.log import logger
from app.ontology.plan import (
    SemanticPlan, Measure, DimensionFilter, DimensionGroupBy,
    TimeRange, JoinSpec,
)


# --------------------------------------------------------------------------- #
# Pure merge logic (testable, no DB / no LLM)
# --------------------------------------------------------------------------- #
def apply_clarification_to_plan(
    initial_plan: dict,
    user_response: dict,
    *,
    today: Optional[date] = None,
) -> dict:
    """Merge user clarification response into the initial plan dict.

    Args:
        initial_plan: SemanticPlan dict (from ask_clarification).
        user_response: dict with optional keys:
            - selections: {measure, time, group_by, dimension}
            - free_text:  str (LLM-parsed into the above later; for
              now we only honor `selections`)
            - confirmed:  bool
        today: override for deterministic testing.

    Returns:
        New plan dict (does NOT mutate input).
    """
    plan = _dict_to_plan_obj(initial_plan)
    selections = (user_response or {}).get("selections") or {}

    # 1. Measure: replace if specified.
    if "measure" in selections and selections["measure"]:
        new_term = selections["measure"]
        if new_term in MEASURE_REGISTRY:
            spec = MEASURE_REGISTRY[new_term]
            plan.measures = [Measure(
                business_term=new_term,
                class_id=spec["class_id"],
                column=spec["column"],
                aggregation=spec["aggregation"],
                pre_filters=list(spec.get("pre_filters", [])),
            )]
        else:
            plan.notes.append(f"unknown measure '{new_term}' ignored")

    # 2. Time: parse and set.
    if "time" in selections and selections["time"]:
        time_text = selections["time"]
        time_range = parse_time_expression(time_text, today=today)
        if time_range:
            start, end = time_range
            plan.time = TimeRange(
                column=plan.time.column if plan.time else "t.dt",
                start=start.isoformat(), end=end.isoformat(),
            )
        else:
            plan.notes.append(f"could not parse time '{time_text}'")

    # 3. Group_by: user picked dimension classes.
    if "group_by" in selections and selections["group_by"]:
        # Non-empty only: an empty list means "no info" (free-text parser
        # emits it opportunistically) - it must NOT wipe the rule-path
        # grouping (e.g. slice-pattern C050 on "Top regions by sales").
        gb_classes = selections["group_by"]
        if not isinstance(gb_classes, list):
            gb_classes = [gb_classes] if gb_classes else []
        # Clear existing group_by (replacement semantics).
        plan.group_by = []
        for cid in gb_classes:
            if not cid:
                continue
            col_info = DIM_VALUE_COLUMN_REGISTRY.get(cid)
            if not col_info:
                plan.notes.append(f"unknown group_by class '{cid}' ignored")
                continue
            plan.group_by.append(DimensionGroupBy(
                class_id=cid, column=col_info["column"],
            ))
            # Ensure JOIN path exists for this dim class.
            if plan.measures:
                key = (plan.measures[0].class_id, cid)
                if key in JOIN_REGISTRY:
                    expected_table = col_info["table"].split(".")[-1]
                    has_join = any(
                        expected_table in j.right_table_real
                        for j in plan.joins
                    )
                    if not has_join:
                        for j in JOIN_REGISTRY[key]:
                            plan.joins.append(JoinSpec(**j))

    # 4. Dimension (filter) — currently optional, mostly auto-filled
    # from match_dimension_value. Allowed via selections for completeness.
    if "dimension" in selections and selections["dimension"]:
        dim_spec = selections["dimension"]
        if isinstance(dim_spec, dict):
            plan.dimensions.append(DimensionFilter(
                class_id=dim_spec.get("class_id", ""),
                column=dim_spec.get("column", ""),
                operator=dim_spec.get("operator", "="),
                value=dim_spec.get("value"),
            ))

    # 4.5 Source adaptation: user selections may combine an ads_total
    # measure with region/category/user grouping - re-source before the
    # JOIN_REGISTRY chains (built for dwd facts) render invalid SQL.
    try:
        adapt_measure_source(plan)
    except Exception as _e:
        logger.debug(f"adapt_measure_source skipped: {_e}")

    # 5. Re-evaluate confidence.
    plan.confidence = _recompute_confidence(plan)
    if plan.pop:
        # 环比 needs window functions - the deterministic render path would
        # silently drop the comparison semantics. Keep the plan on the LLM
        # route (this was lost on clarify-resume: 0.5 -> 0.9 -> plain SUM).
        plan.confidence = min(plan.confidence, 0.5)
        plan.notes.append("pop preserved after merge -> LLM path")
    plan.grounding_source = "hybrid"  # rule + user input

    return plan.to_dict()


def _recompute_confidence(plan: SemanticPlan) -> float:
    """Bump confidence based on field completeness after merge."""
    if not plan.measures:
        return 0.3
    has_time = plan.time is not None
    has_dim_or_group = bool(plan.dimensions or plan.group_by)
    if has_time and has_dim_or_group:
        return 1.0
    if has_time:
        return 0.9
    if has_dim_or_group:
        return 0.7
    return 0.7  # measure present but nothing else


def _dict_to_plan_obj(d: dict) -> SemanticPlan:
    """Reconstruct SemanticPlan from dict form. Delegates to
    SemanticPlan.from_dict (also restores order_by/limit/having that
    this local copy used to drop)."""
    from app.ontology.plan import SemanticPlan
    return SemanticPlan.from_dict(d)



# --------------------------------------------------------------------------- #
# LangGraph node wrapper
# --------------------------------------------------------------------------- #
async def merge_clarification(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Node entry point: merge user response into plan and write back
    to state.semantic_plan.

    Expected state on entry (set by clarify_router on resume):
        - state["clarify_user_response"]: dict from POST body
        - state["clarify_session"]: ClarifySession dict (initial_plan)
        - state["pending_clarify_id"]: clarify_id being resolved
    """
    writer = runtime.stream_writer
    writer({"stage": "Merge Clarification"})

    user_response = state.get("clarify_user_response") or {}
    session_data = state.get("clarify_session") or {}
    initial_plan = session_data.get("initial_plan") or state.get("semantic_plan") or {}

    if not initial_plan:
        logger.warning("merge_clarification: no initial_plan in state; skipping")
        return {"semantic_plan": None, "pending_clarify_id": None}

    try:
        merged_plan = apply_clarification_to_plan(initial_plan, user_response)
    except Exception as e:
        logger.error(f"merge_clarification failed: {e}")
        # Pass through initial plan; downstream will fall to LLM path.
        return {"semantic_plan": initial_plan, "pending_clarify_id": None}

    logger.info(
        f"merge_clarification: confidence {initial_plan.get('confidence', 0)} "
        f"→ {merged_plan.get('confidence', 0)}"
    )
    writer({
        "clarify_merged": {
            "confidence_before": initial_plan.get("confidence"),
            "confidence_after": merged_plan.get("confidence"),
            "notes": merged_plan.get("notes", []),
        },
    })

    return {
        "semantic_plan": merged_plan,
        "pending_clarify_id": None,  # clear the pending flag
    }
