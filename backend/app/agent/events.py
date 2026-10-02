"""Node-side event emission helper (PRD M1 FR4).

Each graph node records 1-2 lines via `emit(...)`; the session id is
the request id from the HTTP contextvar, so no context plumbing is
needed inside the graph. Fire-and-forget: never raises, never blocks.
"""
from __future__ import annotations

from app.core.context import request_id_ctx_var
from app.core.session_event import session_event_logger


def emit(type_: str, payload: dict | None = None) -> None:
    """Append one session event (no-op without a request context)."""
    try:
        sid = request_id_ctx_var.get()
        if not sid:
            return
        session_event_logger.bind(sid).append(type_, payload)
    except Exception:
        pass


async def emit_and_flush(type_: str, payload: dict | None = None) -> None:
    """Append then flush this session's events (used at turn end so the
    event timeline is complete before the client polls)."""
    try:
        sid = request_id_ctx_var.get()
        if not sid:
            return
        bound = session_event_logger.bind(sid)
        bound.append(type_, payload)
        await bound.flush()
    except Exception:
        pass
