"""Approval ticket repository.

Persistence layer for the PII-gate approval workflow. Tickets are
created by `wait_approval` graph node, decided via the REST API, and
the resumed/aborted graph reads them back to continue execution.

All methods are defensive (return None / 0 on DB errors) so a missing
DDL doesn't crash the agent pipeline - the wait_approval node degrades
to "auto-approve with warning" if the table is unavailable.
"""
import json
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import logger


class ApprovalTicket:
    """In-memory snapshot of one ticket row."""
    def __init__(self, ticket_id: str, request_id: str, sql_text: str,
                 username: str = "", pii_reason: str = "",
                 pii_violations: Optional[list] = None,
                 status: str = "pending", decided_by: str = "",
                 decided_at: Optional[str] = None,
                 decision_note: str = "", created_at: str = ""):
        self.ticket_id = ticket_id
        self.request_id = request_id
        self.sql_text = sql_text
        self.username = username
        self.pii_reason = pii_reason
        self.pii_violations = pii_violations or []
        self.status = status
        self.decided_by = decided_by
        self.decided_at = decided_at
        self.decision_note = decision_note
        self.created_at = created_at

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "request_id": self.request_id,
            "username": self.username,
            "sql_text": self.sql_text,
            "pii_reason": self.pii_reason,
            "pii_violations": self.pii_violations,
            "status": self.status,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "decision_note": self.decision_note,
            "created_at": self.created_at,
        }


class ApprovalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        request_id: str,
        sql_text: str,
        username: str = "",
        pii_reason: str = "",
        pii_violations: Optional[list] = None,
    ) -> Optional[ApprovalTicket]:
        """Insert a pending ticket. Returns the created snapshot or None
        on failure."""
        ticket_id = str(uuid.uuid4())
        violations_json = json.dumps(pii_violations or [], ensure_ascii=False)
        try:
            await self.session.execute(text("""
                INSERT INTO data_agent.approval_ticket
                    (ticket_id, request_id, username, sql_text,
                     pii_reason, pii_violations, status, created_at, updated_at)
                VALUES (:tid, :rid, :u, :sql, :reason, :viol,
                        'pending', :ts, :ts)
            """), {
                "tid": ticket_id, "rid": request_id, "u": username,
                "sql": sql_text, "reason": pii_reason, "viol": violations_json,
                "ts": datetime.now(),
            })
            await self.session.commit()
            return ApprovalTicket(
                ticket_id=ticket_id, request_id=request_id,
                sql_text=sql_text, username=username,
                pii_reason=pii_reason, pii_violations=pii_violations or [],
                status="pending", created_at=str(datetime.now()),
            )
        except Exception as e:
            logger.warning(f"Approval ticket create failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
            return None

    async def get(self, ticket_id: str) -> Optional[ApprovalTicket]:
        try:
            rows = await self.session.execute(text("""
                SELECT ticket_id, request_id, username, sql_text,
                       pii_reason, pii_violations, status,
                       decided_by, decided_at, decision_note, created_at
                FROM data_agent.approval_ticket WHERE ticket_id = :tid
            """), {"tid": ticket_id})
            r = rows.fetchone()
            if not r:
                return None
            try:
                violations = json.loads(r[5]) if r[5] else []
            except (ValueError, TypeError):
                violations = []
            return ApprovalTicket(
                ticket_id=r[0], request_id=r[1], username=r[2],
                sql_text=r[3], pii_reason=r[4], pii_violations=violations,
                status=r[6], decided_by=r[7] or "", decided_at=str(r[8]) if r[8] else None,
                decision_note=r[9] or "", created_at=str(r[10]) if r[10] else "",
            )
        except Exception as e:
            logger.warning(f"Approval ticket get failed: {e}")
            return None

    async def get_by_request(self, request_id: str) -> Optional[ApprovalTicket]:
        """Most recent ticket for a request_id (used by the resume path)."""
        try:
            rows = await self.session.execute(text("""
                SELECT ticket_id, request_id, username, sql_text,
                       pii_reason, pii_violations, status,
                       decided_by, decided_at, decision_note, created_at
                FROM data_agent.approval_ticket WHERE request_id = :rid
                ORDER BY created_at DESC LIMIT 1
            """), {"rid": request_id})
            r = rows.fetchone()
            if not r:
                return None
            try:
                violations = json.loads(r[5]) if r[5] else []
            except (ValueError, TypeError):
                violations = []
            return ApprovalTicket(
                ticket_id=r[0], request_id=r[1], username=r[2],
                sql_text=r[3], pii_reason=r[4], pii_violations=violations,
                status=r[6], decided_by=r[7] or "", decided_at=str(r[8]) if r[8] else None,
                decision_note=r[9] or "", created_at=str(r[10]) if r[10] else "",
            )
        except Exception as e:
            logger.warning(f"Approval ticket get_by_request failed: {e}")
            return None

    async def list_pending(self, limit: int = 50) -> list[ApprovalTicket]:
        try:
            rows = await self.session.execute(text("""
                SELECT ticket_id, request_id, username, sql_text,
                       pii_reason, pii_violations, status,
                       decided_by, decided_at, decision_note, created_at
                FROM data_agent.approval_ticket WHERE status = 'pending'
                ORDER BY created_at DESC LIMIT :n
            """), {"n": limit})
            out = []
            for r in rows.fetchall():
                try:
                    violations = json.loads(r[5]) if r[5] else []
                except (ValueError, TypeError):
                    violations = []
                out.append(ApprovalTicket(
                    ticket_id=r[0], request_id=r[1], username=r[2],
                    sql_text=r[3], pii_reason=r[4], pii_violations=violations,
                    status=r[6], decided_by=r[7] or "", decided_at=str(r[8]) if r[8] else None,
                    decision_note=r[9] or "", created_at=str(r[10]) if r[10] else "",
                ))
            return out
        except Exception as e:
            logger.warning(f"Approval ticket list failed: {e}")
            return []

    async def decide(
        self, ticket_id: str, decision: str,
        decided_by: str = "", decision_note: str = "",
    ) -> Optional[ApprovalTicket]:
        """Set a pending ticket to `approved` or `rejected`. Other
        decisions (e.g. `expired`) are also allowed for ops/cleanup.
        Returns the updated ticket or None on failure."""
        decision = (decision or "").lower()
        if decision not in ("approved", "rejected", "expired"):
            return None
        try:
            await self.session.execute(text("""
                UPDATE data_agent.approval_ticket
                SET status = :status, decided_by = :db,
                    decided_at = :ts, decision_note = :note,
                    updated_at = :ts
                WHERE ticket_id = :tid AND status = 'pending'
            """), {
                "tid": ticket_id, "status": decision,
                "db": decided_by, "note": decision_note,
                "ts": datetime.now(),
            })
            await self.session.commit()
            return await self.get(ticket_id)
        except Exception as e:
            logger.warning(f"Approval ticket decide failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
            return None
