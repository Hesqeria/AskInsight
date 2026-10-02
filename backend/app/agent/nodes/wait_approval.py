"""wait_approval node: gate SQL execution behind a human-in-the-loop
approval when the PII policy triggers.

Strategy
--------
The LangGraph streaming model is request-scoped: a single HTTP/SSE
request drives the graph from START to END. Blocking inside the graph
to wait for a human approver is impractical (HTTP timeouts, idle
worker), so this node does three things instead:

  1. **Persists a pending `approval_ticket`** capturing the SQL + PII
     violations + agent request_id. The caller receives a `ticket_id`
     via the SSE stream and can poll `/api/approvals/{ticket_id}`.

  2. **Sets `state["pending_approval_ticket_id"]`** so the conditional
     edge after this node routes to END (terminating this request). The
     SSE `{"stage": "Pending Approval", "ticket_id": ...}` frame tells
     the UI to swap into the approval view.

  3. **Degrades gracefully** when the approval table is unavailable:
     auto-approves with a warning so the agent never deadlocks on a
     dev/CI box that hasn't applied the DDL.

When an approver later POSTs a decision, the API layer re-invokes the
graph with the same state at the `execute_sql` node (see
`api/routers/approval_router.py`).
"""
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.context import request_id_ctx_var
from app.core.log import logger
from app.core.metrics import PII_GATE_DECISIONS


async def wait_approval(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Persist a pending ticket and signal the graph to terminate.

    The actual approval happens out-of-band via the REST API; the
    resumed graph skips this node by going straight to execute_sql."""
    writer = runtime.stream_writer
    writer({"stage": "Pending Approval"})

    sql = state.get("sql", "")
    reason = state.get("approval_reason", "")
    needs_approval = bool(state.get("needs_approval"))

    if not needs_approval:
        # Defensive: the conditional edge shouldn't route here unless
        # the gate triggered, but be tolerant.
        return {"pending_approval_ticket_id": None}

    request_id = request_id_ctx_var.get() or ""
    username = state.get("_username", "anonymous")

    # Coerce the meta repo into an ApprovalRepository (shared session).
    approval_repo = _coerce_approval_repo(runtime.context.get("meta_doris_repository"))
    if approval_repo is None:
        # No DB / table missing -> auto-approve so we don't deadlock.
        logger.warning(
            "wait_approval: no approval repo wired; auto-approving "
            "(apply conf/ddl/approval_schema.sql to enable the gate)"
        )
        PII_GATE_DECISIONS.labels(outcome="auto_approved").inc()
        return {
            "pending_approval_ticket_id": None,
            # Clear the gate so the resumed graph runs execute_sql.
            "needs_approval": False,
            "approval_auto_approved": True,
            # Sticky: without this the re-entered execute_sql would
            # re-ask the PII guard and loop.
            "_pii_approved": True,
        }

    # Build a compact violation list from the approval_reason (already
    # includes class names + levels from correct_sql).
    violations = _extract_violation_dicts(reason)

    # M7: consult the approval POLICY before creating a ticket.
    # never -> fail-closed reject; role allowance -> auto-approve;
    # otherwise ask (legacy ticket flow).
    from app.services.approval_policy import (
        evaluate, resolve_role, emit_policy_event, emit_asked_event,
        emit_decided_event, AUTO_APPROVE, REJECT,
    )
    role = resolve_role(username)
    decision = evaluate(role, len(violations))
    emit_policy_event(decision)
    if decision.action == REJECT:
        logger.warning(f"approval policy rejected: {decision.reason}")
        emit_decided_event("", "rejected", "policy", decision.reason)
        PII_GATE_DECISIONS.labels(outcome="policy_rejected").inc()
        return {
            "pending_approval_ticket_id": None,
            "needs_approval": False,
            "error": f"审批策略拒绝: {decision.reason}",
        }
    if decision.action == AUTO_APPROVE:
        logger.info(f"approval policy auto-approved: {decision.reason}")
        emit_decided_event("", "auto_approved", "policy", decision.reason)
        PII_GATE_DECISIONS.labels(outcome="policy_auto_approved").inc()
        return {
            "pending_approval_ticket_id": None,
            "needs_approval": False,
            "approval_auto_approved": True,
            "_pii_approved": True,
        }

    try:
        ticket = await approval_repo.create(
            request_id=request_id,
            sql_text=sql,
            username=username,
            pii_reason=reason,
            pii_violations=violations,
        )
    except Exception as e:
        logger.warning(f"wait_approval: ticket create raised: {e}")
        ticket = None
    if ticket is None:
        # DB write failed - degrade to auto-approve rather than hanging.
        logger.warning("wait_approval: ticket create failed; auto-approving")
        PII_GATE_DECISIONS.labels(outcome="auto_approved").inc()
        return {
            "pending_approval_ticket_id": None,
            "needs_approval": False,
            "approval_auto_approved": True,
            "_pii_approved": True,
        }

    logger.info(
        f"wait_approval: created ticket {ticket.ticket_id} for "
        f"request={request_id} user={username}"
    )
    emit_asked_event(ticket.ticket_id, request_id, reason, violations)
    # M3: unified inbox suspend (kind=approval) alongside the ticket.
    inbox_id = None
    try:
        from app.services.inbox_service import inbox_service
        _sess = getattr(approval_repo, "session", None)
        if _sess is not None:
            inbox_id = await inbox_service.suspend(
                _sess, request_id, "approval",
                {"ref_id": ticket.ticket_id,
                 "resume_node": "execute_sql", "reason": reason},
            )
    except Exception as e:
        logger.warning(f"wait_approval inbox suspend skipped: {e}")
    writer({
        "pending_approval": {
            "ticket_id": ticket.ticket_id,
            "request_id": request_id,
            "reason": reason,
            "violations": violations,
            "status": "pending",
            "inbox_id": inbox_id,
            "respond_url": (f"/api/v1/inbox/{inbox_id}/respond"
                            if inbox_id else None),
            "poll_url": f"/api/approvals/{ticket.ticket_id}",
            "decide_url": f"/api/approvals/{ticket.ticket_id}/decide",
        },
    })

    # State used by the conditional edge to route to END.
    return {"pending_approval_ticket_id": ticket.ticket_id}


def _extract_violation_dicts(reason: str) -> list[dict]:
    """Best-effort parse of the human-readable reason string back into
    structured violation dicts for the UI. The correct_sql node formats
    each violation as `column_ref (class=Name, pii_level=N)`; we use
    that pattern. Returns [] if parsing fails."""
    if not reason:
        return []
    import re
    # Match `db.table.column (class=Name, pii_level=N)` - column_ref
    # must be word chars + dots only so we don't slurp leading prose.
    pattern = re.compile(
        r'([\w.]+)\s*\(class=([^,]+),\s*pii_level=(\d+)\)'
    )
    out = []
    for m in pattern.finditer(reason):
        out.append({
            "column_ref": m.group(1).strip(),
            "class_name": m.group(2).strip(),
            "pii_level": int(m.group(3)),
        })
    return out


def _coerce_approval_repo(meta_repo):
    """Same trick as correct_sql._coerce_ontology_repo: reuse the
    MetaDorisRepository's session to build an ApprovalRepository."""
    if meta_repo is None:
        return None
    session = getattr(meta_repo, "session", None)
    if session is None:
        return None
    try:
        from app.repositories.doris.approval.approval_repository import ApprovalRepository
        return ApprovalRepository(session)
    except Exception:
        return None
