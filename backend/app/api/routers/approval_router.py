"""Approval REST API.

Endpoints
---------
  GET   /api/approvals                    list pending tickets
  GET   /api/approvals/{ticket_id}        ticket detail (status poll)
  POST  /api/approvals/{ticket_id}/decide approve / reject / expire
  POST  /api/approvals/{ticket_id}/resume re-execute the approved SQL
                                            (continues the agent graph
                                             from execute_sql onward)

Auth
----
- L3_engineer and above can decide tickets (PRD dev-prd/P1-04).
- Any authenticated user can read tickets they raised (filtered by
  request_id at the UI layer; this API returns all for admin).

Failure modes
-------------
- DDL not applied: endpoints return 503 with a friendly message rather
  than crashing. The graph's wait_approval node auto-approves in this
  case so the agent pipeline keeps flowing.
"""
from typing import Optional

from sqlalchemy import text
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.core.auth import verify_token
from app.core.log import logger
from app.repositories.doris.approval.approval_repository import ApprovalRepository

approval_router = APIRouter()


_APPROVER_ROLES = {"L3_engineer", "L4_admin", "admin"}


# --------------------------------------------------------------------------- #
# GET /api/approvals - list pending
# --------------------------------------------------------------------------- #
@approval_router.get("/api/approvals")
async def list_pending_approvals(
    limit: int = 50,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = ApprovalRepository(session)
    tickets = await repo.list_pending(limit=limit)
    return {
        "count": len(tickets),
        "tickets": [t.to_dict() for t in tickets],
    }


# --------------------------------------------------------------------------- #
# GET /api/approvals/{ticket_id} - status poll
# --------------------------------------------------------------------------- #
@approval_router.get("/api/approvals/{ticket_id}")
async def get_approval(
    ticket_id: str,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = ApprovalRepository(session)
    ticket = await repo.get(ticket_id)
    if ticket is None:
        return JSONResponse(status_code=404, content={
            "error": f"ticket {ticket_id!r} not found"
        })
    return ticket.to_dict()


# --------------------------------------------------------------------------- #
# POST /api/approvals/{ticket_id}/decide
# --------------------------------------------------------------------------- #
class DecisionRequest(BaseModel):
    decision: str            # "approved" / "rejected" / "expired"
    note: str = ""


@approval_router.post("/api/approvals/{ticket_id}/decide")
async def decide_approval(
    ticket_id: str,
    body: DecisionRequest,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Approve or reject a pending ticket. Requires L3_engineer role."""
    if user.get("role") not in _APPROVER_ROLES:
        return JSONResponse(status_code=403, content={
            "status": "error",
            "message": "L3_engineer role or above required to decide tickets",
        })

    repo = ApprovalRepository(session)
    existing = await repo.get(ticket_id)
    if existing is None:
        return JSONResponse(status_code=404, content={
            "status": "error", "message": f"ticket {ticket_id!r} not found",
        })
    if existing.status != "pending":
        return JSONResponse(status_code=409, content={
            "status": "error",
            "message": f"ticket already {existing.status!r} (decided_by={existing.decided_by})",
            "ticket": existing.to_dict(),
        })

    # PRD P5: separation of duties - the ticket owner must not approve
    # their own ticket (override for dev/e2e via APPROVAL_SELF_OK=1).
    import os as _os
    _decider = user.get("sub", "unknown")
    if (
        body.decision == "approved"
        and existing.username
        and existing.username == _decider
        and _os.getenv("APPROVAL_SELF_OK", "") != "1"
    ):
        return JSONResponse(status_code=403, content={
            "status": "error",
            "message": (
                f"separation-of-duties: owner {existing.username!r} "
                f"cannot approve their own ticket (set APPROVAL_SELF_OK=1 to override)"
            ),
        })

    updated = await repo.decide(
        ticket_id=ticket_id,
        decision=body.decision,
        decided_by=_decider,
        decision_note=body.note,
    )
    if updated is None:
        return JSONResponse(status_code=500, content={
            "status": "error", "message": "decide failed (see server logs)",
        })

    # PRD P5: pre-execution snapshot - freeze the SQL exactly as approved
    # so any post-approval mutation is detectable.
    if updated.status == "approved":
        try:
            await session.execute(text(
                "UPDATE data_agent.approval_ticket SET snapshot_sql = sql_text "
                "WHERE ticket_id = :tid"), {"tid": ticket_id})
            await session.commit()
        except Exception as snap_err:
            logger.warning(f"snapshot_sql update failed: {snap_err}")

    logger.info(
        f"approval {ticket_id} decided={updated.status} "
        f"by={updated.decided_by}"
    )
    try:
        from app.services.approval_policy import emit_decided_event
        emit_decided_event(ticket_id, updated.status,
                           updated.decided_by, body.note)
    except Exception:
        pass
    return {
        "status": "ok",
        "ticket": updated.to_dict(),
        # Hint to the client: if approved, call /resume to continue execution.
        "next_action": "resume" if updated.status == "approved" else None,
        "resume_url": (f"/api/approvals/{ticket_id}/resume"
                       if updated.status == "approved" else None),
    }


# --------------------------------------------------------------------------- #
# POST /api/approvals/{ticket_id}/resume - re-execute approved SQL
# --------------------------------------------------------------------------- #
@approval_router.post("/api/approvals/{ticket_id}/resume")
async def resume_approved_sql(
    ticket_id: str,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Continue execution for an approved ticket.

    Returns an SSE stream mirroring the original /api/query endpoint.

    Implementation note: LangGraph doesn't support `start_at` for a
    compiled graph in this version. Rather than rebuild the graph with
    a subgraph API, the resume stream calls the execute_sql + downstream
    nodes directly in linear order. This is correct because the path
    `execute_sql -> extract_lineage -> anomaly -> drill -> decision ->
    code_executor` has no conditional branches - if any of those nodes
    ever grows a branch, the resume path must be revisited.

    The approved SQL is self-contained - execute_sql + downstream nodes
    only need the SQL + the dw repo, so we don't reconstruct the full
    DataAgentState (no retrieved_columns / table_infos etc.)."""
    repo = ApprovalRepository(session)
    ticket = await repo.get(ticket_id)
    if ticket is None:
        return JSONResponse(status_code=404, content={
            "status": "error", "message": f"ticket {ticket_id!r} not found",
        })
    if ticket.status != "approved":
        return JSONResponse(status_code=409, content={
            "status": "error",
            "message": f"ticket status is {ticket.status!r}, must be 'approved'",
        })

    from fastapi.responses import StreamingResponse
    from app.agent.state import DataAgentState
    from app.clients.doris_client_manager import doris_client_manager
    from app.core.context import request_id_ctx_var
    from app.agent.nodes.execute_sql import execute_sql
    from app.agent.nodes.extract_lineage import extract_lineage
    from app.agent.nodes.anomaly_detection import anomaly_detection
    from app.agent.nodes.drill_down_analysis import drill_down_analysis
    from app.agent.nodes.decision_insight import decision_insight
    from app.agent.nodes.code_executor import code_executor

    from app.agent.state import DataAgentState
    from app.services.resume_engine import run_node_stream

    async def stream():
        import json
        request_id_ctx_var.set(ticket.request_id)
        try:
            state = DataAgentState(
                query=ticket.pii_reason or "[approved resume]",
                sql=ticket.sql_text,
                error=None,
                needs_approval=False,
                pending_approval_ticket_id=None,
                # Sticky approval: resumed execute_sql skips the ask.
                _pii_approved=True,
                _username=ticket.username or user.get("sub", "anonymous"),
            )
            username = ticket.username or user.get("sub", "anonymous")
            async for line in run_node_stream(
                    state, "execute_sql", username,
                    session_id=ticket.request_id,
                    extra_complete={"ticket_id": ticket_id}):
                yield line
        except Exception as e:
            logger.error(f"resume stream failed: {e}", exc_info=True)
            yield "data: " + json.dumps(
                {"error": str(e)}, ensure_ascii=False,
            ) + "\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")



