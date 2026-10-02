"""Approval policy service (PRD M7).

dsh insight: approval = ApprovalPolicy('ask'|'never') + policy events.
AskInsight mapping:

- evaluate(): the PII gate (M6 GuardAsk) consults the POLICY of the
  query RAISER's role before creating a ticket:
      never mode  -> reject outright (fail-closed, no ticket)
      auto_approve roles (admin) -> pass without a ticket
      max_pii_without_approval   -> pass when violation count is within
                                    the role's allowance
      everyone else -> ask (create ticket + SSE, the legacy flow)
- Events (M1): approval/policy (decision + reason), approval/asked,
  approval/decided - "replaying the log IS the state".
- expire_pending(): tickets older than expire_hours become expired
  (called by the background sweeper in main.py lifespan).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text

from app.core.log import logger

ASK = "ask"
AUTO_APPROVE = "auto_approve"
REJECT = "reject"


@dataclass
class PolicyDecision:
    action: str                 # ask | auto_approve | reject
    reason: str = ""
    role: str = ""
    violations: int = 0
    extra: dict = field(default_factory=dict)


def resolve_role(username: str) -> str:
    """Username -> role using the auth module's convention (admin is the
    only locally-known privileged user; others are plain users)."""
    return "admin" if username in ("admin",) else "user"


def evaluate(role: str, violation_count: int, mode: str = None) -> PolicyDecision:
    """Policy decision for a PII-gate trigger (M6 GuardAsk -> M7)."""
    from app.conf.app_config import app_config
    cfg = app_config.approval
    mode = (mode or cfg.mode or "ask").lower()
    if mode == "never":
        # Strict mode: the gate is fail-closed - no ticket, no bypass.
        return PolicyDecision(
            action=REJECT, role=role, violations=violation_count,
            reason="审批策略为严格模式(never):含高敏字段的 SQL 被直接拒绝",
            extra={"mode": mode},
        )
    rule = cfg.rule_for(role)
    if rule.auto_approve:
        return PolicyDecision(
            action=AUTO_APPROVE, role=role, violations=violation_count,
            reason=f"角色 {role} 免审(approval.roles.{role}.auto_approve)",
            extra={"mode": mode},
        )
    if (rule.max_pii_without_approval >= 0
            and violation_count <= rule.max_pii_without_approval):
        return PolicyDecision(
            action=AUTO_APPROVE, role=role, violations=violation_count,
            reason=(f"角色 {role} 允许 PII 列数 "
                    f"{rule.max_pii_without_approval} 内免审"
                    f"(当前 {violation_count})"),
            extra={"mode": mode},
        )
    # Default: full review (L1/user 等).
    return PolicyDecision(
        action=ASK, role=role, violations=violation_count,
        reason=f"角色 {role} 需人工审批({violation_count} 个高敏列)",
        extra={"mode": mode},
    )


async def expire_pending(session, expire_hours: int = None) -> list[str]:
    """Flip tickets pending longer than expire_hours to expired.

    Returns the expired ticket ids (callers emit approval/decided events
    and discard the matching inbox items)."""
    from app.conf.app_config import app_config
    hours = expire_hours if expire_hours is not None else \
        app_config.approval.expire_hours
    if not hours or hours <= 0:
        return []
    cutoff = datetime.now() - timedelta(hours=hours)
    try:
        rows = await session.execute(text("""
            SELECT ticket_id FROM data_agent.approval_ticket
            WHERE status = 'pending' AND created_at < :cutoff
        """), {"cutoff": cutoff})
        tids = [r[0] for r in rows.fetchall()]
        if not tids:
            return []
        await session.execute(text("""
            UPDATE data_agent.approval_ticket
            SET status = 'expired', decided_by = 'system:sweeper',
                decided_at = :ts, decision_note = 'expired by policy sweeper',
                updated_at = :ts
            WHERE status = 'pending' AND created_at < :cutoff
        """), {"ts": datetime.now(), "cutoff": cutoff})
        await session.commit()
        return tids
    except Exception as e:
        logger.warning(f"approval expire_pending failed: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        return []


def emit_policy_event(decision: PolicyDecision, ticket_id: str = "") -> None:
    """approval/policy -> M1 log (never raises)."""
    try:
        from app.agent.events import emit
        emit("approval/policy", {
            "action": decision.action, "role": decision.role,
            "violations": decision.violations, "reason": decision.reason,
            "ticket_id": ticket_id, **decision.extra,
        })
    except Exception:
        pass


def emit_decided_event(ticket_id: str, decision: str, by: str,
                       reason: str = "") -> None:
    """approval/decided -> M1 log (never raises)."""
    try:
        from app.agent.events import emit
        emit("approval/decided", {
            "ticket_id": ticket_id, "decision": decision, "by": by,
            "reason": reason[:200],
        })
    except Exception:
        pass


def emit_asked_event(ticket_id: str, session_id: str,
                     reason: str, violations: list) -> None:
    """approval/asked -> M1 log (never raises)."""
    try:
        from app.agent.events import emit
        emit("approval/asked", {
            "ticket_id": ticket_id, "session_id": session_id,
            "reason": reason[:300], "violations": violations[:10],
        })
    except Exception:
        pass
