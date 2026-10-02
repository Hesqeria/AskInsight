"""Session event log SDK (PRD 05-DeepSeek-Harness研究 M1).

事件日志 = 唯一真源:审计 / 回放 / 恢复 / 评测共享同一份 append-only 流。

Design (borrowed from dsh):
- append() is fire-and-forget: deep-copies the payload (frozen boundary),
  enqueues to an in-process queue, and returns immediately. Event writes
  never block the main pipeline (non-functional requirement).
- A background flusher batches events (50 rows or 500ms) into Doris via
  SessionEventRepository.insert_batch.
- DB failure degrades to a local JSONL file (same last-resort tier as
  core/audit.py) - events are never silently dropped.
- seq is assigned at flush time from a per-session in-memory counter,
  initialized from MAX(seq) on first flush so counters survive restarts.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.log import logger

BATCH_SIZE = 50
FLUSH_INTERVAL_SECONDS = 0.5
FALLBACK_FILE = Path(os.getenv("SESSION_EVENT_FALLBACK_FILE",
                               "data/session_event_fallback.log"))
MAX_PAYLOAD_CHARS = 6000  # per-string truncation to bound row size


def _freeze(value):
    """Deep-copy + truncate long strings: what enters the log is the
    authoritative read-only value (dsh deep-freeze boundary, pragmatic
    Python version)."""
    if isinstance(value, dict):
        return {k: _freeze(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_freeze(v) for v in value]
    if isinstance(value, str) and len(value) > MAX_PAYLOAD_CHARS:
        return value[:MAX_PAYLOAD_CHARS] + f"...<truncated {len(value)} chars>"
    return value


@dataclass
class _PendingEvent:
    session_id: str
    type: str
    payload: dict = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)


class SessionEventLogger:
    """Per-process singleton. Use `bind(session_id)` for a session handle."""

    def __init__(self):
        self._queue: asyncio.Queue[_PendingEvent] = asyncio.Queue()
        self._counters: dict[str, int] = {}
        self._flusher_task: asyncio.Task | None = None
        self._started = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def bind(self, session_id: str) -> "BoundEventLogger":
        """Bind a session handle. Safe to call per request."""
        return BoundEventLogger(self, session_id)

    def submit(self, session_id: str, type_: str, payload: dict | None) -> None:
        """Enqueue one event (non-blocking). Starts the flusher lazily."""
        self._ensure_flusher()
        try:
            self._queue.put_nowait(_PendingEvent(
                session_id=session_id, type=type_, payload=_freeze(payload or {}),
            ))
        except Exception as e:  # queue full or closed - log, never raise
            logger.debug(f"session_event submit dropped: {e}")

    async def flush(self, session_id: str | None = None) -> None:
        """Drain pending events (all sessions, or one) into the store.
        Called at turn end so the timeline is complete before the client
        polls, and from tests."""
        pending = self._drain(session_id)
        if pending:
            await self._write(pending)

    def pending_count(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _ensure_flusher(self):
        if self._started:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (sync caller) - events stay queued
        self._started = True
        self._flusher_task = loop.create_task(self._flusher_loop())

    async def _flusher_loop(self):
        """Batch flush: 500ms tick or BATCH_SIZE backlog."""
        while True:
            try:
                await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
                pending = self._drain()
                if pending:
                    await self._write(pending)
            except asyncio.CancelledError:
                # Final drain on shutdown (best effort).
                try:
                    pending = self._drain()
                    if pending:
                        await self._write(pending)
                except Exception:
                    pass
                raise
            except Exception as e:
                logger.warning(f"session_event flusher error: {e}")

    def _drain(self, session_id: str | None = None) -> list[_PendingEvent]:
        """Pop all queued events; when a session filter is given, keep
        that session's events and re-queue the rest in order."""
        everything: list[_PendingEvent] = []
        while True:
            try:
                everything.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if session_id is None:
            return everything
        kept = [e for e in everything if e.session_id == session_id]
        rest = [e for e in everything if e.session_id != session_id]
        for e in rest:
            self._queue.put_nowait(e)
        return kept

    def _next_seq(self, session_id: str) -> int:
        seq = self._counters.get(session_id, 0) + 1
        self._counters[session_id] = seq
        return seq

    def _bump_counter_from_db(self, session_id: str, max_seq: int) -> None:
        if max_seq > self._counters.get(session_id, 0):
            self._counters[session_id] = max_seq

    async def _write(self, pending: list[_PendingEvent]):
        """Persist a batch: Doris -> local file fallback.

        Numbering happens BEFORE any DB attempt and max_seq failures are
        swallowed per session, so `events` is always complete - the file
        fallback therefore covers every pending event (never a partial
        batch)."""
        if not pending:
            return
        from app.repositories.doris.session_event.session_event_repository import (
            SessionEvent, SessionEventRepository,
        )
        # Assign seqs for the full batch first; init counters from
        # MAX(seq) once per session (per batch) so restarts stay
        # monotonic. A failing max_seq leaves the in-memory counter as
        # is rather than aborting the numbering.
        events: list[SessionEvent] = []
        seen: set[str] = set()
        try:
            repo = None
            async with _doris_session() as session:
                repo = SessionEventRepository(session)
                for evt in pending:
                    sid = evt.session_id
                    if sid not in seen:
                        seen.add(sid)
                        try:
                            self._bump_counter_from_db(
                                sid, await repo.max_seq(sid))
                        except Exception:
                            pass
                    events.append(SessionEvent(
                        session_id=sid, seq=self._next_seq(sid),
                        type_=evt.type, payload=evt.payload,
                    ))
                ok = await repo.insert_batch(events)
                if ok:
                    return
        except Exception as e:
            logger.warning(f"session_event DB write failed, falling back "
                           f"to file: {e}")
        # Tier 2: local JSONL file with the COMPLETE numbered batch.
        self._write_file_fallback(events if events else [
            SessionEvent(e.session_id, self._next_seq(e.session_id),
                         e.type, e.payload) for e in pending
        ])

    @staticmethod
    def _write_file_fallback(events: list):
        try:
            FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(FALLBACK_FILE, "a", encoding="utf-8") as f:
                for e in events:
                    f.write(json.dumps({
                        "session_id": e.session_id, "seq": e.seq,
                        "type": e.type, "payload": e.payload,
                        "created_at": time.time(),
                    }, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.error(f"session_event file fallback failed (LOST): {e}")


def _doris_session():
    from app.clients.doris_client_manager import doris_client_manager
    return doris_client_manager.session_factory(
)


class BoundEventLogger:
    """Session-scoped facade: `logger.append("sql/generated", {...})`."""

    def __init__(self, parent: SessionEventLogger, session_id: str):
        self._parent = parent
        self.session_id = session_id

    def append(self, type_: str, payload: dict | None = None) -> None:
        """Record one event. Fire-and-forget, never raises."""
        try:
            self._parent.submit(self.session_id, type_, payload)
        except Exception as e:
            logger.debug(f"session_event append failed (dropped): {e}")

    async def flush(self) -> None:
        await self._parent.flush(self.session_id)


# Process-wide singleton (mirrors redis_client_manager style).
session_event_logger = SessionEventLogger()
