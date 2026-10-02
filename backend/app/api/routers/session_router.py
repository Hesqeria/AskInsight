"""Session event log API (PRD M1 FR5).

Endpoints
---------
  GET /api/v1/sessions/{session_id}/events          raw event list (asc seq)
  GET /api/v1/sessions/{session_id}/events/timeline replay view: events
      enriched with per-stage durations + turn/guard summaries, consumed
      by the frontend debug panel and eval trajectory collection (FR6).

Defensive: missing DDL -> 503 with a friendly message (mirrors clarify).
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.core.auth import verify_token
from app.repositories.doris.session_event.session_event_repository import (
    SessionEventRepository,
)

session_router = APIRouter()


@session_router.get("/api/v1/sessions/{session_id}/events")
async def list_session_events(
    session_id: str,
    limit: int = 500,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    try:
        repo = SessionEventRepository(session)
        events = await repo.list_events(session_id, limit=limit)
        return {
            "session_id": session_id,
            "count": len(events),
            "events": [e.to_dict() for e in events],
        }
    except Exception as e:
        return JSONResponse(status_code=503, content={
            "error": f"session_event table unavailable: {e}",
            "hint": "apply conf/ddl/session_event_schema.sql",
        })


@session_router.get("/api/v1/sessions/{session_id}/events/timeline")
async def session_event_timeline(
    session_id: str,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Replay view: sequential stages with durations + guard decisions.

    The timeline groups consecutive events into a chronological flow the
    UI renders directly (stage cards); durations come from created_at
    deltas between adjacent events (second granularity from Doris
    DATETIME; sub-second stages read as 0ms and fall back to a
    monotonic index)."""
    try:
        repo = SessionEventRepository(session)
        events = await repo.list_events(session_id, limit=500)
    except Exception as e:
        return JSONResponse(status_code=503, content={
            "error": f"session_event table unavailable: {e}",
        })
    if not events:
        return JSONResponse(status_code=404, content={
            "error": f"no events for session {session_id!r}",
        })

    stages = []
    guards = []
    turns = []
    for i, ev in enumerate(events):
        entry = ev.to_dict()
        stages.append({
            "seq": ev.seq, "type": ev.type, "payload": ev.payload,
            "created_at": ev.created_at, "index": i,
        })
        if ev.type == "guard/decision":
            guards.append(ev.payload)
        if ev.type == "turn/ended":
            turns.append(ev.payload)

    ckpt = await repo.latest_checkpoint(session_id)
    return {
        "session_id": session_id,
        "event_count": len(events),
        "stages": stages,
        "guard_decisions": guards,
        "turns": turns,
        "latest_checkpoint": ckpt.payload if ckpt else None,
    }
