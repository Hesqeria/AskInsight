"""Agent inbox repository (PRD M3).

One table for every async interaction (clarify / approval / future
dimension-value confirmation...). Defensive on DB errors like the
clarify/approval repos: a missing DDL degrades to None / [] so the
graph never deadlocks (the domain tables remain the source of truth
for their own views; the inbox is the unified control plane).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import logger


class InboxItem:
    __slots__ = ("inbox_id", "session_id", "channel", "kind", "payload",
                 "status", "created_at", "claimed_at", "resolved_at")

    def __init__(self, inbox_id: str, session_id: str, channel: str,
                 kind: str, payload: Optional[dict] = None,
                 status: str = "pending", created_at: str = "",
                 claimed_at: Optional[str] = None,
                 resolved_at: Optional[str] = None):
        self.inbox_id = inbox_id
        self.session_id = session_id
        self.channel = channel
        self.kind = kind
        self.payload = payload or {}
        self.status = status
        self.created_at = created_at
        self.claimed_at = claimed_at
        self.resolved_at = resolved_at

    def to_dict(self) -> dict:
        return {
            "inbox_id": self.inbox_id, "session_id": self.session_id,
            "channel": self.channel, "kind": self.kind,
            "payload": self.payload, "status": self.status,
            "created_at": self.created_at, "claimed_at": self.claimed_at,
            "resolved_at": self.resolved_at,
        }


class InboxRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, session_id: str, kind: str,
                     payload: dict, channel: str = "next_step",
                     ) -> Optional[InboxItem]:
        inbox_id = f"ibx_{uuid.uuid4().hex[:12]}"
        ts = datetime.now()
        # Enforce "one pending next_step per session": an existing
        # pending interaction queues this one as next_turn.
        channel = await self._resolve_channel(session_id, channel)
        try:
            await self.session.execute(text("""
                INSERT INTO data_agent.agent_inbox
                    (id, session_id, channel, kind, payload, status,
                     created_at)
                VALUES (:id, :sid, :ch, :kind, :payload, 'pending', :ts)
            """), {
                "id": inbox_id, "sid": session_id, "ch": channel,
                "kind": kind,
                "payload": json.dumps(payload or {}, ensure_ascii=False),
                "ts": ts,
            })
            await self.session.commit()
            return InboxItem(inbox_id, session_id, channel, kind, payload,
                             created_at=str(ts))
        except Exception as e:
            logger.warning(f"agent_inbox create failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
            return None

    async def _resolve_channel(self, session_id: str, channel: str) -> str:
        if channel != "next_step":
            return channel
        existing = await self.list_pending(session_id)
        if any(i.channel == "next_step" for i in existing):
            return "next_turn"
        return channel

    async def get(self, inbox_id: str) -> Optional[InboxItem]:
        try:
            result = await self.session.execute(text("""
                SELECT id, session_id, channel, kind, payload, status,
                       created_at, claimed_at, resolved_at
                FROM data_agent.agent_inbox WHERE id = :id
            """), {"id": inbox_id})
            row = result.mappings().first()
            if row is None:
                return None
            try:
                payload = json.loads(row["payload"] or "{}")
            except Exception:
                payload = {}
            return InboxItem(
                row["id"], row["session_id"], row["channel"], row["kind"],
                payload, row["status"], str(row["created_at"] or ""),
                str(row["claimed_at"] or "") or None,
                str(row["resolved_at"] or "") or None,
            )
        except Exception as e:
            logger.warning(f"agent_inbox get failed: {e}")
            return None

    async def list_pending(self, session_id: Optional[str] = None,
                           limit: int = 50) -> list[InboxItem]:
        try:
            if session_id:
                result = await self.session.execute(text("""
                    SELECT id, session_id, channel, kind, payload, status,
                           created_at, claimed_at, resolved_at
                    FROM data_agent.agent_inbox
                    WHERE status = 'pending' AND session_id = :sid
                    ORDER BY created_at ASC LIMIT :lim
                """), {"sid": session_id, "lim": limit})
            else:
                result = await self.session.execute(text("""
                    SELECT id, session_id, channel, kind, payload, status,
                           created_at, claimed_at, resolved_at
                    FROM data_agent.agent_inbox
                    WHERE status = 'pending'
                    ORDER BY created_at ASC LIMIT :lim
                """), {"lim": limit})
            out = []
            for row in result.mappings():
                try:
                    payload = json.loads(row["payload"] or "{}")
                except Exception:
                    payload = {}
                out.append(InboxItem(
                    row["id"], row["session_id"], row["channel"],
                    row["kind"], payload, row["status"],
                    str(row["created_at"] or ""),
                    str(row["claimed_at"] or "") or None,
                    str(row["resolved_at"] or "") or None,
                ))
            return out
        except Exception as e:
            logger.warning(f"agent_inbox list_pending failed: {e}")
            return []

    async def mark(self, inbox_id: str, status: str) -> Optional[InboxItem]:
        ts = datetime.now()
        try:
            await self.session.execute(text("""
                UPDATE data_agent.agent_inbox
                SET status = :st,
                    claimed_at = CASE WHEN :st = 'claimed'
                                      THEN :ts ELSE claimed_at END,
                    resolved_at = CASE WHEN :st IN ('done', 'discarded')
                                       THEN :ts ELSE resolved_at END
                WHERE id = :id
            """), {"st": status, "ts": ts, "id": inbox_id})
            await self.session.commit()
            return await self.get(inbox_id)
        except Exception as e:
            logger.warning(f"agent_inbox mark failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
            return None
