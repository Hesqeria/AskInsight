"""Session event repository (PRD M1).

Append-only event log for one agent session (session_id == request_id).
All methods are defensive: a missing DDL or DB error degrades to None /
[] so the event log never breaks the main pipeline.
"""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import logger


class SessionEvent:
    """In-memory snapshot of one session_event row."""

    __slots__ = ("session_id", "seq", "type", "payload", "created_at")

    def __init__(self, session_id: str, seq: int, type_: str,
                 payload: Optional[dict] = None, created_at: str = ""):
        self.session_id = session_id
        self.seq = seq
        self.type = type_
        self.payload = payload or {}
        self.created_at = created_at

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "seq": self.seq,
            "type": self.type,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class SessionEventRepository:
    """CRUD on the session_event table (append-only by convention)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert_batch(self, events: list[SessionEvent]) -> bool:
        """Insert a batch of events. Returns True on success."""
        if not events:
            return True
        rows = [
            {
                "sid": e.session_id, "seq": e.seq, "type": e.type,
                "payload": json.dumps(e.payload, ensure_ascii=False, default=str),
            }
            for e in events
        ]
        try:
            await self.session.execute(text("""
                INSERT INTO data_agent.session_event (session_id, seq, type, payload)
                VALUES (:sid, :seq, :type, :payload)
            """), rows)
            await self.session.commit()
            return True
        except Exception as e:
            logger.warning(f"session_event batch insert failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
            return False

    async def list_events(self, session_id: str,
                          limit: int = 500) -> list[SessionEvent]:
        """List events of a session ordered by seq (replay source)."""
        try:
            result = await self.session.execute(text("""
                SELECT seq, type, payload, created_at
                FROM data_agent.session_event
                WHERE session_id = :sid
                ORDER BY seq ASC
                LIMIT :lim
            """), {"sid": session_id, "lim": limit})
            out = []
            for row in result.mappings():
                try:
                    payload = json.loads(row["payload"] or "{}")
                except Exception:
                    payload = {"_raw": str(row["payload"] or "")}
                out.append(SessionEvent(
                    session_id=session_id, seq=row["seq"], type_=row["type"],
                    payload=payload, created_at=str(row["created_at"] or ""),
                ))
            return out
        except Exception as e:
            logger.warning(f"session_event list failed: {e}")
            return []

    async def max_seq(self, session_id: str) -> int:
        """MAX(seq) for a session; used to resume the in-memory counter
        after a process restart."""
        try:
            result = await self.session.execute(text("""
                SELECT MAX(seq) AS m FROM data_agent.session_event WHERE session_id = :sid
            """), {"sid": session_id})
            row = result.mappings().first()
            return int(row["m"] or 0) if row else 0
        except Exception as e:
            logger.debug(f"session_event max_seq failed: {e}")
            return 0

    async def latest_checkpoint(self, session_id: str) -> Optional[SessionEvent]:
        """Most recent state/checkpoint event (M3 resume source)."""
        try:
            result = await self.session.execute(text("""
                SELECT seq, type, payload, created_at
                FROM data_agent.session_event
                WHERE session_id = :sid AND type = 'state/checkpoint'
                ORDER BY seq DESC
                LIMIT 1
            """), {"sid": session_id})
            row = result.mappings().first()
            if row is None:
                return None
            try:
                payload = json.loads(row["payload"] or "{}")
            except Exception:
                return None
            return SessionEvent(
                session_id=session_id, seq=row["seq"], type_=row["type"],
                payload=payload, created_at=str(row["created_at"] or ""),
            )
        except Exception as e:
            logger.warning(f"session_event latest_checkpoint failed: {e}")
            return None
