"""ask_clarification node: ask user to disambiguate when the
semantic_grounding node produces a low-confidence plan.

Strategy (mirrors wait_approval)
--------------------------------
LangGraph streaming is request-scoped, so blocking inside the graph to
wait for the user is impractical. This node:

  1. **Persists a pending `clarify_session`** capturing the initial
     plan + missing fields + suggested options. The caller receives a
     `clarify_id` via SSE and POSTs the user's response to
     `/api/v1/clarify/{clarify_id}/resume`.

  2. **Sets `state["pending_clarify_id"]`** so the conditional edge
     routes to END (terminating this request). The SSE
     `{"stage": "Pending Clarification", ...}` frame tells the UI to
     swap into the clarification card view.

  3. **Degrades gracefully** when the clarify table is unavailable:
     skips clarification and lets generate_sql run (marking the plan
     as low-confidence so the LLM path is used).

Decision rule (PRD §3.1)
------------------------
Triggered when `state.semantic_plan.confidence < 0.7`. The node then
detects *which* fields are missing/ambiguous and builds the suggestion
cards accordingly.
"""
from __future__ import annotations

from typing import Optional

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.context import request_id_ctx_var
from app.core.log import logger

# Confidence threshold below which we must clarify.
CLARIFY_CONFIDENCE_THRESHOLD = 0.7
# Hard cap on clarification rounds to prevent infinite loops.
MAX_CLARIFY_ROUNDS = 3


# --------------------------------------------------------------------------- #
# Decision function: does this plan need clarification?
# --------------------------------------------------------------------------- #
def needs_clarification(state: DataAgentState) -> bool:
    """Conditional edge router: True if plan confidence is below
    threshold OR a required field is missing.

    Used directly as the conditional-edge lambda in graph.py.
    """
    plan = state.get("semantic_plan")
    if not plan:
        return False  # No plan = no grounding happened; fall through to LLM
    import os as _os
    if _os.getenv("ABLATION_FORCE_LLM") == "1":
        # research ablation: the LLM lane must attempt every question
        return False
    # Pop (period-over-period / dual-period compare) is routed to the LLM
    # window-function lane on purpose - its sub-threshold confidence must
    # not trigger a clarification round.
    if plan.get("pop"):
        return False
    confidence = plan.get("confidence", 0) or 0
    if confidence < CLARIFY_CONFIDENCE_THRESHOLD:
        return True
    # Even with high confidence, check for missing critical fields.
    missing = detect_missing_fields(plan)
    return len(missing) > 0


def detect_missing_fields(plan: dict) -> list[dict]:
    """Inspect the plan and return a list of missing/ambiguous fields.

    Each entry: {field, reason, current_value}.
    `field` ∈ {"measure", "time", "dimension", "group_by"}.
    `reason` ∈ {"missing", "ambiguous", "low_confidence"}.
    """
    out: list[dict] = []
    if not plan:
        return out

    measures = plan.get("measures") or []
    if not measures:
        out.append({
            "field": "measure", "reason": "missing", "current_value": None,
        })
        # No measure means nothing downstream makes sense to ask about.
        return out

    time_range = plan.get("time")
    if not time_range or not time_range.get("start") or not time_range.get("end"):
        out.append({
            "field": "time", "reason": "missing", "current_value": time_range,
        })

    # Dimensions are not required, but if user said a region-like token
    # and we didn't match it, that's a missing-dimension signal.
    # Heuristic: if confidence < 0.7 AND no dimensions matched, ask.
    dimensions = plan.get("dimensions") or []
    confidence = plan.get("confidence", 1.0) or 1.0
    # Pop (period-over-period / dual-period compare) questions go to the
    # LLM window-function lane; a dimension is genuinely not required.
    if not dimensions and confidence < CLARIFY_CONFIDENCE_THRESHOLD             and not plan.get("pop"):
        out.append({
            "field": "dimension", "reason": "low_confidence",
            "current_value": None,
        })

    return out


# --------------------------------------------------------------------------- #
# Suggestion generators
# --------------------------------------------------------------------------- #
def build_suggestions(
    plan: dict, missing_fields: list[dict],
) -> list[dict]:
    """Build the list of suggestion groups to surface to the user.

    Each group: {field, prompt, options, multi_select, allow_custom}.
    Options are sourced from existing registries (no DB hits).
    """
    from app.agent.nodes.semantic_grounding import (
        MEASURE_REGISTRY, _BUSINESS_TERM_ALIASES,
    )

    groups: list[dict] = []
    if not missing_fields:
        return groups

    # Identify which fields to ask about.
    ask_fields = {m["field"] for m in missing_fields}

    # 1. Measure
    if "measure" in ask_fields or _is_ambiguous_measure(plan):
        # Source options from registry + aliases.
        seen = set()
        options = []
        # Put recommended (most common) first.
        for term in ("GMV", "order_count", "DAU"):
            if term in MEASURE_REGISTRY and term not in seen:
                spec = MEASURE_REGISTRY[term]
                options.append({
                    "label": f"{term} ({_measure_short_name(term)})",
                    "value": term,
                    "description": _measure_description(term),
                    "recommended": term == "GMV",
                })
                seen.add(term)
        # Remaining registry terms.
        for term, spec in MEASURE_REGISTRY.items():
            if term in seen:
                continue
            options.append({
                "label": f"{term} ({_measure_short_name(term)})",
                "value": term,
                "description": _measure_description(term),
            })
            seen.add(term)
        groups.append({
            "field": "measure",
            "prompt": "您想看哪个指标?",
            "options": options,
            "multi_select": False,
            "allow_custom": True,
        })

    # 2. Time
    if "time" in ask_fields:
        groups.append({
            "field": "time",
            "prompt": "需要补充时间范围:",
            "options": [
                {"label": "今天",   "value": "今天",  "description": "今日数据"},
                {"label": "昨天",   "value": "昨天",  "description": "昨日数据"},
                {"label": "近7天",  "value": "近7天", "description": "过去一周"},
                {"label": "本月",   "value": "本月",  "description": "本月累计"},
                {"label": "上月",   "value": "上月",  "description": "上月完整月份"},
                {"label": "本季度", "value": "本季度", "description": "当前季度"},
            ],
            "multi_select": False,
            "allow_custom": True,
        })

    # 3. Dimension (group-by / filter)
    if "dimension" in ask_fields:
        groups.append({
            "field": "group_by",
            "prompt": "是否需要按维度分组?(可选)",
            "options": [
                {"label": "不分组",  "value": None, "description": "只看总量"},
                {"label": "按地区",  "value": "C050", "description": "按地域维度"},
                {"label": "按商品",  "value": "C020", "description": "按商品/SKU维度"},
                {"label": "按客户",  "value": "C011", "description": "按用户维度"},
                {"label": "按品类",  "value": "C021", "description": "按品类维度"},
            ],
            "multi_select": True,
            "allow_custom": False,
        })

    return groups


def _is_ambiguous_measure(plan: dict) -> bool:
    """Detect when a measure exists but the user wording is ambiguous.

    Heuristic: if the user typed any of the alias keywords for multiple
    canonical terms, they probably mean one of N things.
    """
    # Simplified: when confidence is exactly 0.5, treat as ambiguous.
    return plan.get("confidence", 1.0) == 0.5


def _measure_short_name(term: str) -> str:
    return {
        "GMV":         "总成交金额",
        "DAU":         "日活跃用户",
        "order_count": "订单数",
    }.get(term, term)


def _measure_description(term: str) -> str:
    return {
        "GMV":         "已支付订单的金额总和(不含退款)",
        "DAU":         "当日有访问/操作的去重用户数",
        "order_count": "订单总数量",
    }.get(term, "")


# --------------------------------------------------------------------------- #
# LangGraph node wrapper
# --------------------------------------------------------------------------- #
async def ask_clarification(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Persist a pending clarify_session and signal the graph to END.

    The actual user response comes out-of-band via
    `/api/v1/clarify/{id}/resume`; the resumed graph skips this node
    by starting at merge_clarification.
    """
    writer = runtime.stream_writer
    writer({"stage": "Pending Clarification"})

    plan = state.get("semantic_plan") or {}
    question = state.get("query", "")
    username = state.get("_username", "anonymous")

    missing = detect_missing_fields(plan)
    suggestions = build_suggestions(plan, missing)

    # --- Multi-path candidate generation + rerank (多路重排) ----------
    # Build complete candidate plans from the ambiguous question, blend
    # confidence + rerank relevance + history preference, then attach
    # them to the clarify card so the user can pick a whole interpretation.
    candidates = await _build_candidates(state, runtime, question)

    # Defensive: if nothing to ask AND no candidates, fall through.
    if not suggestions and not candidates:
        logger.info("ask_clarification: nothing to ask; falling through")
        return {"pending_clarify_id": None}

    # Get clarify repository (mirror wait_approval's coercion pattern).
    clarify_repo = _coerce_clarify_repo(runtime.context.get("meta_doris_repository"))
    if clarify_repo is None:
        # No DB / table missing -> skip clarification.
        logger.warning(
            "ask_clarification: no clarify repo wired; skipping "
            "(apply conf/ddl/clarify_schema.sql to enable)"
        )
        return {
            "pending_clarify_id": None,
            "clarify_skipped": True,
        }

    try:
        # Persist candidates alongside the field-level suggestions so the
        # resume endpoint can resolve a picked candidate_id.
        persist_payload = {
            "field_suggestions": suggestions,
            "candidates": candidates,
        }
        session = await clarify_repo.create(
            question=question,
            initial_plan=plan,
            missing_fields=missing,
            suggestions_given=persist_payload,
            username=username,
            rounds=1,
            request_id=request_id_ctx_var.get() or "",
        )
    except Exception as e:
        logger.warning(f"ask_clarification: session create raised: {e}")
        session = None

    if session is None:
        logger.warning("ask_clarification: session create failed; skipping")
        return {
            "pending_clarify_id": None,
            "clarify_skipped": True,
        }

    # A/B metric: a clarification was triggered.
    try:
        from app.core.metrics import CLARIFY_TRIGGERED
        reason = "low_confidence" if (plan.get("confidence", 1) or 1) < CLARIFY_CONFIDENCE_THRESHOLD else "missing_field"
        CLARIFY_TRIGGERED.labels(reason=reason).inc()
    except Exception:
        pass

    logger.info(
        f"ask_clarification: created session {session.clarify_id} for "
        f"question='{question[:50]}' missing={[m['field'] for m in missing]}"
    )
    from app.agent.events import emit
    emit("plan/clarified", {
        "clarify_id": session.clarify_id,
        "missing_fields": [m.get("field") for m in missing],
        "rounds": 1,
        "action": "asked",
    })
    # M3: unified inbox suspend (kind=clarify) alongside the domain
    # table - the inbox is the generic respond/resume front door.
    inbox_id = None
    try:
        from app.services.inbox_service import inbox_service
        meta_repo = runtime.context.get("meta_doris_repository")
        _sess = getattr(meta_repo, "session", None)
        if _sess is not None:
            inbox_id = await inbox_service.suspend(
                _sess, request_id_ctx_var.get() or "", "clarify",
                {"ref_id": session.clarify_id,
                 "resume_node": "generate_sql", "question": question},
            )
    except Exception as e:
        logger.warning(f"ask_clarification inbox suspend skipped: {e}")
    writer({
        "clarify_required": {
            "clarify_id": session.clarify_id,
            "question": question,
            "missing_fields": missing,
            "suggestions": suggestions,
            "candidates": candidates,   # 多路完整方案(重排后)
            "current_summary": _quick_summary(plan),
            "resume_url": f"/api/v1/clarify/{session.clarify_id}/resume",
            "inbox_id": inbox_id,
            "respond_url": (f"/api/v1/inbox/{inbox_id}/respond"
                            if inbox_id else None),
            "status": "pending",
        },
    })

    # State used by the conditional edge to route to END.
    return {"pending_clarify_id": session.clarify_id}


async def _build_candidates(state: DataAgentState, runtime: Runtime[DataAgentContext],
                            question: str) -> list[dict]:
    """Generate + rank multi-path candidate plans.

    Ordering blends confidence + rerank relevance + history preference.
    On any failure returns [] (the clarify card still works via the
    simple field-level suggestions).
    """
    try:
        from app.agent.candidate_generator import generate_candidates
        from app.agent.candidate_ranker import CandidateRanker

        candidates = generate_candidates(
            question=question,
            keywords=state.get("keywords", []),
            matched_dimension_values=state.get("matched_dimension_values", []),
            glossary_matches=state.get("glossary_matches", []),
        )
        if not candidates:
            return []

        # Rerank client from runtime context (same one used by RAG merge).
        rerank_client = runtime.context.get("rerank_client")

        # History preference: past confirmed measures from clarify_feedback.
        history = await _load_history_preference(runtime)

        ranker = CandidateRanker(rerank_client=rerank_client, history=history)
        ranked = await ranker.rank(question, candidates, top_n=4)
        logger.info(
            f"ask_clarification: generated {len(candidates)} candidates, "
            f"ranked to {len(ranked)}"
        )
        return ranked
    except Exception as e:
        logger.warning(f"ask_clarification: candidate gen failed: {e}")
        return []


async def _load_history_preference(runtime) -> list[dict]:
    """Load recent confirmed clarify_feedback samples to bias ranking.

    Returns a list of plan dicts (the `final_plan` column). Best-effort;
    [] on any error.
    """
    try:
        meta_repo = runtime.context.get("meta_doris_repository")
        if meta_repo is None:
            return []
        session = getattr(meta_repo, "session", None)
        if session is None:
            return []
        from app.repositories.doris.clarify.clarify_feedback_repository import (
            ClarifyFeedbackRepository,
        )
        fb_repo = ClarifyFeedbackRepository(session)
        samples = await fb_repo.list_samples(limit=20)
        plans = [s.get("final_plan") or {} for s in samples]
        return [p for p in plans if p.get("measures")]
    except Exception as e:
        logger.debug(f"ask_clarification: history pref load failed: {e}")
        return []


def _quick_summary(plan: dict) -> str:
    """Brief one-line summary for the SSE chunk. Reuses explain layer
    when available; falls back to a minimal summary otherwise."""
    try:
        from app.ontology.plan import (
            SemanticPlan, Measure, DimensionFilter, DimensionGroupBy,
            TimeRange, JoinSpec,
        )
        from app.ontology.explain import to_business_summary
        plan_obj = _dict_to_plan_obj(plan)
        return to_business_summary(plan_obj)
    except Exception:
        # Minimal fallback.
        if not plan:
            return "未建立语义计划"
        measures = plan.get("measures") or []
        if not measures:
            return "未识别到明确的指标,请补充"
        m = measures[0]
        return f"查询 {m.get('business_term', '?')} ({m.get('aggregation', '?')})"


def _dict_to_plan_obj(d: dict):
    """Reconstruct SemanticPlan from dict form. Delegates to
    SemanticPlan.from_dict (single source of truth)."""
    from app.ontology.plan import SemanticPlan
    return SemanticPlan.from_dict(d)



def _coerce_clarify_repo(meta_repo):
    """Same trick as wait_approval._coerce_approval_repo: reuse the
    MetaDorisRepository's session to build a ClarifyRepository."""
    if meta_repo is None:
        return None
    session = getattr(meta_repo, "session", None)
    if session is None:
        return None
    try:
        from app.repositories.doris.clarify.clarify_repository import ClarifyRepository
        return ClarifyRepository(session)
    except Exception:
        return None
