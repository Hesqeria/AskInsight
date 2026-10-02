"""Clarification REST API.

Endpoints
---------
  GET   /api/v1/clarify                    list pending clarify sessions
  GET   /api/v1/clarify/{clarify_id}       session detail (status poll)
  POST  /api/v1/clarify/{clarify_id}/resume submit user response + resume graph

The resume endpoint mirrors `approval_router.resume_approved_sql` but
starts the resumed graph at `merge_clarification` (not execute_sql),
since clarification happens BEFORE SQL generation.

Failure modes
-------------
- DDL not applied: GET endpoints return 503, POST /resume returns 503
  with a friendly message.
- Session not found / wrong status: 404 / 409.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.core.auth import verify_token
from app.core.log import logger
from app.repositories.doris.clarify.clarify_repository import ClarifyRepository


clarify_router = APIRouter()


# --------------------------------------------------------------------------- #
# GET /api/v1/clarify - list pending
# --------------------------------------------------------------------------- #
@clarify_router.get("/api/v1/clarify")
async def list_pending_clarify(
    limit: int = 50,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = ClarifyRepository(session)
    sessions = await repo.list_pending(limit=limit)
    return {
        "count": len(sessions),
        "sessions": [s.to_dict() for s in sessions],
    }


# --------------------------------------------------------------------------- #
# GET /api/v1/clarify/{clarify_id} - status poll
# --------------------------------------------------------------------------- #
@clarify_router.get("/api/v1/clarify/{clarify_id}")
async def get_clarify(
    clarify_id: str,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = ClarifyRepository(session)
    s = await repo.get(clarify_id)
    if s is None:
        return JSONResponse(status_code=404, content={
            "error": f"clarify session {clarify_id!r} not found"
        })
    return s.to_dict()


# --------------------------------------------------------------------------- #
# POST /api/v1/clarify/{clarify_id}/resume
# --------------------------------------------------------------------------- #
class ClarifyResumeRequest(BaseModel):
    response_type: str = Field("selection", description="selection|free_text|cancel|candidate")
    selections: dict = Field(default_factory=dict,
                              description="{measure, time, group_by, dimension}")
    free_text: Optional[str] = Field(None, description="free-form user input")
    confirmed: bool = Field(True, description="user confirmed or wants more edits")
    candidate_id: Optional[str] = Field(None,
                                        description="selected candidate plan id (multi-path)")


@clarify_router.post("/api/v1/clarify/{clarify_id}/resume")
async def resume_clarify(
    clarify_id: str,
    req: ClarifyResumeRequest,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Submit user response to a clarification session and resume the graph.

    Returns an SSE stream that runs merge_clarification → generate_sql
    → validate_sql → ... → END (same downstream path as a normal query,
    but starting from the merged plan instead of fresh NL grounding).
    """
    repo = ClarifyRepository(session)
    sess = await repo.get(clarify_id)
    if sess is None:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "message": f"clarify session {clarify_id!r} not found",
        })
    if sess.status != "pending":
        return JSONResponse(status_code=409, content={
            "status": "error",
            "message": f"session status is {sess.status!r}, must be 'pending'",
        })

    # Cancel path: user bailed out.
    if req.response_type == "cancel":
        await repo.update_response(
            clarify_id=clarify_id,
            user_response={"response_type": "cancel"},
            final_plan=sess.initial_plan,
            status="abandoned",
        )
        return {"status": "cancelled", "clarify_id": clarify_id}

    # Build the user_response dict for the merge node.
    user_response = {
        "response_type": req.response_type,
        "selections": req.selections,
        "free_text": req.free_text,
        "confirmed": req.confirmed,
    }

    # Free-text path: parse the user's natural-language answer into
    # structured selections via the LLM (P1-09 自由文本解析).
    if req.response_type == "free_text" and req.free_text:
        from app.agent.free_text_parser import aparse_free_text
        try:
            parsed_selections = await aparse_free_text(sess.question, req.free_text)
            if parsed_selections:
                # Merge: explicit button selections win; free-text fills gaps.
                merged_selections = {**parsed_selections, **req.selections}
                user_response["selections"] = merged_selections
                logger.info(
                    f"clarify free_text parsed: {parsed_selections} "
                    f"for session {clarify_id}"
                )
            else:
                # Parse failed -> re-prompt with buttons.
                logger.warning(
                    f"clarify free_text parse returned empty for {clarify_id}"
                )
                return JSONResponse(status_code=422, content={
                    "status": "parse_failed",
                    "clarify_id": clarify_id,
                    "message": "未能理解您的回复,请从下方选项中选择",
                    "re_prompt": True,
                })
        except Exception as e:
            logger.error(f"free_text parse error: {e}", exc_info=True)
            return JSONResponse(status_code=500, content={
                "status": "error",
                "message": f"自由文本解析失败: {e}",
            })

    # Run merge_clarification locally (pure function) so we can persist
    # the final plan BEFORE resuming the graph. This also lets us
    # short-circuit if the merge fails or status stays "amended".
    from app.agent.nodes.merge_clarification import apply_clarification_to_plan

    # Candidate path: user picked a whole multi-path plan (多路重排).
    if req.response_type == "candidate" and req.candidate_id:
        merged_plan = _resolve_candidate_plan(sess, req.candidate_id)
        if merged_plan is None:
            return JSONResponse(status_code=422, content={
                "status": "error",
                "message": f"候选方案 {req.candidate_id!r} 不存在",
            })
        user_response["candidate_id"] = req.candidate_id
    else:
        try:
            merged_plan = apply_clarification_to_plan(sess.initial_plan, user_response)
        except Exception as e:
            logger.error(f"merge failed for {clarify_id}: {e}")
            return JSONResponse(status_code=500, content={
                "status": "error",
                "message": f"merge failed: {e}",
            })

    # Decide status: confirmed (execute) vs amended (more rounds).
    next_status = "confirmed" if req.confirmed else "amended"
    rounds = sess.rounds + (0 if req.confirmed else 1)
    # PRD §2.3 hard cap: 3 rounds.
    if rounds > 3:
        next_status = "confirmed"  # force-execute
        merged_plan["notes"].append("forced execute after 3 rounds")

    updated = await repo.update_response(
        clarify_id=clarify_id,
        user_response=user_response,
        final_plan=merged_plan,
        status=next_status,
        rounds=rounds,
    )
    if updated is None:
        return JSONResponse(status_code=500, content={
            "status": "error",
            "message": "failed to update session",
        })

    # ---- P3-CLARIFY-15/16: record training sample + A/B metrics ----
    _record_feedback_and_metrics(
        session=session,
        clarify_id=clarify_id,
        question=sess.question,
        initial_confidence=sess.initial_plan.get("confidence"),
        missing_fields=sess.missing_fields,
        user_response=user_response,
        final_plan=merged_plan,
        final_confidence=merged_plan.get("confidence"),
        rounds=rounds,
        outcome=next_status,
        username=user.get("sub", "anonymous"),
    )

    # If user wants more amendments, return the new card (not a stream).
    if next_status == "amended":
        from app.agent.nodes.ask_clarification import (
            detect_missing_fields, build_suggestions,
        )
        missing = detect_missing_fields(merged_plan)
        suggestions = build_suggestions(merged_plan, missing)
        return {
            "status": "amended",
            "clarify_id": clarify_id,
            "merged_plan": merged_plan,
            "missing_fields": missing,
            "suggestions": suggestions,
            "rounds": rounds,
        }

    # Confirmed path: resume the graph from generate_sql onward,
    # streaming SSE chunks like the main /api/query endpoint.
    return StreamingResponse(
        _resume_stream(updated, user),
        media_type="text/event-stream",
    )


def _resolve_candidate_plan(sess, candidate_id: str) -> Optional[dict]:
    """Find a candidate plan dict by its _candidate_id.

    Candidates are persisted inside the clarify_session's
    suggestions_given (which we stored as {field_suggestions, candidates}).
    Returns None if not found (caller returns 422).
    """
    try:
        payload = sess.suggestions_given
        if isinstance(payload, dict):
            candidates = payload.get("candidates") or []
        elif isinstance(payload, list):
            # Older sessions may store a bare list; scan for dicts that
            # look like candidate plans.
            candidates = [c for c in payload if isinstance(c, dict)
                          and c.get("_candidate_id")]
        else:
            candidates = []
        for c in candidates:
            if c.get("_candidate_id") == candidate_id:
                return c
    except Exception as e:
        logger.warning(f"_resolve_candidate_plan failed: {e}")
    return None


def _record_feedback_and_metrics(*, session, clarify_id, question,
                                  initial_confidence, missing_fields,
                                  user_response, final_plan,
                                  final_confidence, rounds, outcome,
                                  username):
    """P3-CLARIFY-15/16: persist a training sample + fire A/B metrics.

    Best-effort; never raises (metrics failures are swallowed, feedback
    insert failures only warn) so the resume path never breaks.
    """
    # A/B metrics (Prometheus)
    try:
        from app.core.metrics import (
            CLARIFY_OUTCOME, CLARIFY_ROUNDS, CLARIFY_CONFIDENCE_DELTA,
        )
        CLARIFY_OUTCOME.labels(outcome=outcome).inc()
        CLARIFY_ROUNDS.observe(max(1, rounds))
        if initial_confidence is not None and final_confidence is not None:
            CLARIFY_CONFIDENCE_DELTA.observe(max(0.0, final_confidence - initial_confidence))
    except Exception as e:
        logger.debug(f"clarify metrics failed: {e}")

    # Training sample persistence (RL / DPO data source)
    try:
        from app.repositories.doris.clarify.clarify_feedback_repository import (
            ClarifyFeedbackRepository,
        )
        fb_repo = ClarifyFeedbackRepository(session)
        fb_repo.record(
            clarify_id=clarify_id,
            question=question,
            initial_confidence=initial_confidence,
            missing_fields=missing_fields,
            user_response=user_response,
            final_plan=final_plan,
            final_confidence=final_confidence,
            rounds=rounds,
            outcome=outcome,
            username=username,
        )
    except Exception as e:
        logger.warning(f"clarify feedback record failed: {e}")


async def _resume_stream(session, user):
    """SSE stream resuming the graph at generate_sql with the merged
    plan. Delegates to the canonical resume engine (mirrors graph.py
    conditional edges incl. assess_complexity / wait_approval)."""
    from app.agent.state import DataAgentState
    from app.clients.doris_client_manager import doris_client_manager
    from app.repositories.doris.clarify.clarify_repository import ClarifyRepository
    from app.services.resume_engine import run_node_stream

    async with doris_client_manager.session_factory() as resume_session:
        # Timeline continuity: append resumed-leg events to the
        # ORIGINAL session's log (best-effort when migration absent).
        try:
            orig_rid = await ClarifyRepository(
                resume_session).get_request_id(session.clarify_id)
        except Exception:
            orig_rid = ""
        state = DataAgentState(
            query=session.question,
            semantic_plan=session.final_plan,
            error=None,
            _username=session.username or user.get("sub", "anonymous"),
        )
        # Rehydrate grounding context (tables/keywords/metrics) from the
        # original turn's state/checkpoint event - without this the resumed
        # generate_sql ran with 0 tables and the LLM produced SQL blind.
        try:
            from sqlalchemy import text as _text
            import json as _json
            row = (await resume_session.execute(_text(
                "SELECT payload FROM data_agent.session_event "
                "WHERE session_id = :rid AND type = 'state/checkpoint' "
                "ORDER BY seq DESC LIMIT 1"), {"rid": orig_rid})).fetchone()
            if row and row[0]:
                ckpt = _json.loads(row[0])
                for key in ("table_infos", "keywords", "metric_infos",
                            "date_info", "db_info", "enriched_query",
                            "glossary_matches", "matched_dimension_values",
                            "retrieved_columns", "retrieved_metrics"):
                    if ckpt.get(key):
                        state[key] = ckpt[key]
                logger.info(f"resume rehydrated grounding context "
                            f"({len(state.get('table_infos') or [])} tables)")
        except Exception as rehy_err:
            logger.warning(f"resume rehydrate skipped: {rehy_err}")
        username = session.username or user.get("sub", "anonymous")
        async for line in run_node_stream(
                state, "generate_sql", username,
                session_id=orig_rid,
                extra_complete={"clarify_id": session.clarify_id}):
            yield line

# --------------------------------------------------------------------------- #
# GET /api/v1/clarify/feedback - training-sample stats (P3-CLARIFY-15)
# --------------------------------------------------------------------------- #
@clarify_router.get("/api/v1/clarify/feedback/stats")
async def clarify_feedback_stats(
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Aggregate stats for clarification feedback (A/B dashboard)."""
    from app.repositories.doris.clarify.clarify_feedback_repository import (
        ClarifyFeedbackRepository,
    )
    repo = ClarifyFeedbackRepository(session)
    stats = await repo.stats()
    return stats


# --------------------------------------------------------------------------- #
# GET /api/v1/clarify/feedback/samples - training-sample export (P3-CLARIFY-15)
# --------------------------------------------------------------------------- #
@clarify_router.get("/api/v1/clarify/feedback/samples")
async def clarify_feedback_samples(
    limit: int = 100,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Export recent clarification training samples (for RL/DPO prep)."""
    from app.repositories.doris.clarify.clarify_feedback_repository import (
        ClarifyFeedbackRepository,
    )
    repo = ClarifyFeedbackRepository(session)
    samples = await repo.list_samples(limit=limit)
    return {"count": len(samples), "samples": samples}
