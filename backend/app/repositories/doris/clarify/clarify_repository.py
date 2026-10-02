"""Clarify session repository.

Persistence layer for the fuzzy-question clarification workflow.
Mirrors `approval_repository` structure: tickets created by
`ask_clarification` graph node, decided via REST API, and the resumed
graph reads them back to merge the user's response and continue.

All methods are defensive (return None / [] on DB errors) so a missing
DDL doesn't crash the agent pipeline - the ask_clarification node
degrades to "skip clarification with warning" if the table is
unavailable.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import logger


# Migration tolerance: deployments that haven't applied
# clarify_add_request_id.sql keep working (create falls back to the
# legacy column list). Probed once per process.
_HAS_REQUEST_ID_COL: bool | None = None


def _insert_sql(has_rid: bool) -> str:
    cols = ("(clarify_id, question, initial_plan, final_plan, "
            "missing_fields, suggestions_given, user_response, "
            "status, rounds, username, request_id, created_at, "
            "updated_at)") if has_rid else            ("(clarify_id, question, initial_plan, final_plan, "
            "missing_fields, suggestions_given, user_response, "
            "status, rounds, username, created_at, updated_at)")
    vals = ("VALUES (:cid, :q, :ip, NULL, :mf, :sg, NULL, "
            "'pending', :r, :u, :rid, :ts, :ts)") if has_rid else            ("VALUES (:cid, :q, :ip, NULL, :mf, :sg, NULL, "
            "'pending', :r, :u, :ts, :ts)")
    return f"INSERT INTO data_agent.clarify_session {cols} {vals}"


async def _probe_request_id_col(session) -> bool:
    global _HAS_REQUEST_ID_COL
    if _HAS_REQUEST_ID_COL is None:
        try:
            from sqlalchemy import text as _text
            await session.execute(_text(
                "SELECT request_id FROM data_agent.clarify_session LIMIT 1"))
            _HAS_REQUEST_ID_COL = True
        except Exception:
            _HAS_REQUEST_ID_COL = False
    return _HAS_REQUEST_ID_COL


class ClarifySession:
    """In-memory snapshot of one clarify_session row."""
    def __init__(
        self,
        clarify_id: str,
        question: str = "",
        request_id: str = "",
        initial_plan: Optional[dict] = None,
        final_plan: Optional[dict] = None,
        missing_fields: Optional[list] = None,
        suggestions_given: Optional[list] = None,
        user_response: Optional[dict] = None,
        status: str = "pending",
        rounds: int = 1,
        username: str = "",
        created_at: str = "",
        resolved_at: Optional[str] = None,
    ):
        self.clarify_id = clarify_id
        self.question = question
        self.request_id = request_id or ""
        self.initial_plan = initial_plan or {}
        self.final_plan = final_plan or {}
        self.missing_fields = missing_fields or []
        self.suggestions_given = suggestions_given or []
        self.user_response = user_response or {}
        self.status = status
        self.rounds = rounds
        self.username = username
        self.created_at = created_at
        self.resolved_at = resolved_at

    def to_dict(self) -> dict:
        return {
            "clarify_id": self.clarify_id,
            "question": self.question,
            "request_id": self.request_id,
            "initial_plan": self.initial_plan,
            "final_plan": self.final_plan,
            "missing_fields": self.missing_fields,
            "suggestions_given": self.suggestions_given,
            "user_response": self.user_response,
            "status": self.status,
            "rounds": self.rounds,
            "username": self.username,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }


class ClarifyRepository:
    """CRUD operations on clarify_session table.

    Defensive: all methods return None / [] on DB errors so the graph
    never hard-fails when this table is missing.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        question: str,
        initial_plan: dict,
        missing_fields: list,
        suggestions_given: list,
        username: str = "",
        rounds: int = 1,
        request_id: str = "",
    ) -> Optional[ClarifySession]:
        """Insert a pending clarify session. Returns the created snapshot."""
        clarify_id = f"clr_{uuid.uuid4().hex[:12]}"
        ts = datetime.now()
        try:
            has_rid = await _probe_request_id_col(self.session)
            params = {
                "cid": clarify_id, "q": question,
                "ip": json.dumps(initial_plan or {}, ensure_ascii=False),
                "mf": json.dumps(missing_fields or [], ensure_ascii=False),
                "sg": json.dumps(suggestions_given or [], ensure_ascii=False),
                "r": rounds, "u": username, "ts": ts,
            }
            if has_rid:
                params["rid"] = request_id or ""
            await self.session.execute(text(_insert_sql(has_rid)), params)
            await self.session.commit()
            return ClarifySession(
                clarify_id=clarify_id, question=question,
                initial_plan=initial_plan, missing_fields=missing_fields,
                suggestions_given=suggestions_given, status="pending",
                rounds=rounds, username=username, created_at=str(ts),
            )
        except Exception as e:
            logger.warning(f"Clarify session create failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
            return None

    async def get_request_id(self, clarify_id: str) -> str:
        """Defensively fetch the originating request_id ("" when the
        column/migration is absent, so old deployments keep working)."""
        if not await _probe_request_id_col(self.session):
            return ""
        try:
            rows = await self.session.execute(text(
                "SELECT request_id FROM data_agent.clarify_session "
                "WHERE clarify_id = :cid"
            ), {"cid": clarify_id})
            r = rows.fetchone()
            return (r[0] or "") if r else ""
        except Exception:
            return ""

    async def get(self, clarify_id: str) -> Optional[ClarifySession]:
        try:
            rows = await self.session.execute(text("""
                SELECT clarify_id, question, initial_plan, final_plan,
                       missing_fields, suggestions_given, user_response,
                       status, rounds, username, created_at, resolved_at
                FROM data_agent.clarify_session WHERE clarify_id = :cid
            """), {"cid": clarify_id})
            r = rows.fetchone()
            if not r:
                return None
            return self._row_to_session(r)
        except Exception as e:
            logger.warning(f"Clarify session get failed: {e}")
            return None

    async def list_pending(self, limit: int = 50) -> list[ClarifySession]:
        try:
            rows = await self.session.execute(text("""
                SELECT clarify_id, question, initial_plan, final_plan,
                       missing_fields, suggestions_given, user_response,
                       status, rounds, username, created_at, resolved_at
                FROM data_agent.clarify_session WHERE status = 'pending'
                ORDER BY created_at DESC LIMIT :n
            """), {"n": limit})
            return [self._row_to_session(r) for r in rows.fetchall()]
        except Exception as e:
            logger.warning(f"Clarify session list failed: {e}")
            return []

    async def update_response(
        self,
        clarify_id: str,
        user_response: dict,
        final_plan: dict,
        status: str = "confirmed",
        rounds: Optional[int] = None,
    ) -> Optional[ClarifySession]:
        """Persist the user's response and the merged plan.

        Called by `/api/v1/clarify/{id}/resume` after merge_clarification
        has produced the final plan. Status transitions:
          - 'confirmed': user accepted, plan can execute
          - 'amended':   user requested changes, more rounds needed
          - 'abandoned': user cancelled
        """
        ts = datetime.now()
        try:
            await self.session.execute(text("""
                UPDATE data_agent.clarify_session
                SET user_response = :ur,
                    final_plan    = :fp,
                    status        = :status,
                    rounds        = COALESCE(:r, rounds),
                    resolved_at   = CASE WHEN :status IN ('confirmed','abandoned')
                                         THEN :ts ELSE resolved_at END,
                    updated_at    = :ts
                WHERE clarify_id = :cid AND status = 'pending'
            """), {
                "cid": clarify_id,
                "ur": json.dumps(user_response or {}, ensure_ascii=False),
                "fp": json.dumps(final_plan or {}, ensure_ascii=False),
                "status": status,
                "r": rounds,
                "ts": ts,
            })
            await self.session.commit()
            return await self.get(clarify_id)
        except Exception as e:
            logger.warning(f"Clarify session update failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
            return None

    def _row_to_session(self, r) -> ClarifySession:
        """Decode JSON columns safely."""
        def _loads(v, default):
            if not v:
                return default
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return default
        return ClarifySession(
            clarify_id=r[0], question=r[1] or "",
            initial_plan=_loads(r[2], {}),
            final_plan=_loads(r[3], {}),
            missing_fields=_loads(r[4], []),
            suggestions_given=_loads(r[5], []),
            user_response=_loads(r[6], {}),
            status=r[7] or "pending",
            rounds=int(r[8] or 1),
            username=r[9] or "",
            created_at=str(r[10]) if r[10] else "",
            resolved_at=str(r[11]) if r[11] else None,
        )
