"""Unified inbox REST API (PRD M3).

Endpoints
---------
  GET  /api/v1/inbox/pending            list pending interactions
  GET  /api/v1/inbox/{inbox_id}         item detail (status poll)
  POST /api/v1/inbox/{inbox_id}/respond submit response; routes by kind
       (clarify -> plan merge, approval -> decide+resume). Body:
       {response: {...}, steer: false}. steer=true injects context
       without answering (dsh steer semantics).

The legacy /api/v1/clarify/{id}/resume and /api/approvals/{id}/resume
endpoints remain and keep working (FR5 compatibility); this API is the
unified front door new interaction kinds plug into.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.core.auth import verify_token
from app.repositories.doris.inbox.inbox_repository import InboxRepository
from app.services.inbox_service import inbox_service, run_resume_stream

inbox_router = APIRouter()


@inbox_router.get("/api/v1/inbox/pending")
async def list_pending(
    session_id: str = "",
    limit: int = 50,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = InboxRepository(session)
    items = await repo.list_pending(session_id or None, limit=limit)
    return {
        "count": len(items),
        "items": [i.to_dict() for i in items],
        "kinds": inbox_service.kinds(),
    }


@inbox_router.get("/api/v1/inbox/{inbox_id}")
async def get_item(
    inbox_id: str,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = InboxRepository(session)
    item = await repo.get(inbox_id)
    if item is None:
        return JSONResponse(status_code=404, content={
            "error": f"inbox item {inbox_id!r} not found"})
    return item.to_dict()


class RespondRequest(BaseModel):
    response: dict = Field(default_factory=dict)
    steer: bool = Field(False, description="inject context, keep pending")


@inbox_router.post("/api/v1/inbox/{inbox_id}/respond")
async def respond(
    inbox_id: str,
    req: RespondRequest,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    username = user.get("sub", "anonymous")
    result = await inbox_service.respond(
        session, inbox_id, req.response, username=username,
        steer=req.steer, role=user.get("role", ""),
    )
    status = result.get("status")
    if status == "forbidden":
        msg = ""
        hr = result.get("handler_result")
        if hr is not None:
            msg = hr.extra.get("message", "forbidden")
        return JSONResponse(status_code=403, content={
            "status": "forbidden", "message": msg,
        })
    if status == "error":
        code = 404 if result.get("error") == "not_found" else \
            409 if result.get("error") == "conflict" else 500
        return JSONResponse(status_code=code, content=result)
    if status == "steered":
        return {"status": "steered", "injected": result.get("injected")}
    hr = result.get("handler_result")
    if hr is None:
        return result
    if hr.sse_stream and hr.resume_node:
        # Timeline continuity: node emit() calls during the resumed leg
        # must append to the ORIGINAL session's event log, not this
        # respond request's fresh id.
        from app.core.context import request_id_ctx_var
        request_id_ctx_var.set(result["item"].get("session_id") or "")
        state = hr.extra.get("state") or {}
        return StreamingResponse(
            run_resume_stream(state, hr.resume_node, username,
                              session_id=result["item"].get("session_id") or ""),
            media_type="text/event-stream",
        )
    # Non-streaming outcomes: amended card / cancelled / rejected / etc.
    return {
        "status": status,
        "kind": result["item"].get("kind"),
        "clarify_id": (result["item"].get("payload") or {}).get("ref_id"),
        "item": result["item"],
        "extra": hr.extra,
    }
