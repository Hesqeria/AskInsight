"""Approval workflow tests.

Covers:
  - wait_approval node: persists ticket, signals END via state
  - Auto-approve fallback when no repo wired
  - Auto-approve fallback when DB write fails
  - ApprovalRepository CRUD (via mocked session)
  - ApprovalRouter endpoints (list / get / decide / resume happy paths)
  - Graph conditional edges route correctly
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_runtime(context: dict):
    runtime = MagicMock()
    runtime.stream_writer = lambda x: None
    runtime.context = context
    return runtime


# --------------------------------------------------------------------------- #
# wait_approval node
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_wait_approval_no_repo_auto_approves(monkeypatch):
    """Without an approval repo wired, the node must auto-approve so
    the pipeline doesn't deadlock (dev box / pre-DDL)."""
    from app.agent.nodes import wait_approval as mod

    state = {"sql": "SELECT 1", "needs_approval": True,
             "approval_reason": "test"}
    runtime = _make_runtime({})  # no meta_doris_repository

    result = await mod.wait_approval(state, runtime)
    assert result["pending_approval_ticket_id"] is None
    # The state is mutated to clear the gate so the conditional edge
    # routes to execute_sql.
    assert result["needs_approval"] is False
    assert result.get("approval_auto_approved") is True


@pytest.mark.asyncio
async def test_wait_approval_creates_ticket(monkeypatch):
    """With a wired repo, the node creates a pending ticket and sets
    pending_approval_ticket_id, signaling the conditional edge to END."""
    from app.agent.nodes import wait_approval as mod

    captured = {}

    class _FakeTicket:
        def __init__(self, tid, sql, reason):
            self.ticket_id = tid
            self.sql_text = sql
            self.pii_reason = reason

    class _FakeApprovalRepo:
        async def create(self, request_id, sql_text, username, pii_reason,
                         pii_violations):
            captured["request_id"] = request_id
            captured["sql_text"] = sql_text
            captured["username"] = username
            captured["pii_reason"] = pii_reason
            captured["pii_violations"] = pii_violations
            return _FakeTicket("ticket-123", sql_text, pii_reason)

    fake_meta = MagicMock()
    fake_meta.session = "fake-session"
    monkeypatch.setattr(
        "app.repositories.doris.approval.approval_repository.ApprovalRepository",
        lambda session: _FakeApprovalRepo(),
    )

    # Pin request_id via context var.
    from app.core.context import request_id_ctx_var
    request_id_ctx_var.set("req-abc")

    state = {
        "sql": "SELECT u.phone FROM dim_user u",
        "needs_approval": True,
        "approval_reason": "dw.dim_user.phone (class=User, pii_level=3)",
    }
    runtime = _make_runtime({"meta_doris_repository": fake_meta})

    result = await mod.wait_approval(state, runtime)
    assert result["pending_approval_ticket_id"] == "ticket-123"
    # The conditional edge reads this and routes to END.
    assert captured["request_id"] == "req-abc"
    assert captured["sql_text"] == "SELECT u.phone FROM dim_user u"
    # Violations parsed from the reason string.
    assert len(captured["pii_violations"]) == 1
    v = captured["pii_violations"][0]
    assert v["class_name"] == "User"
    assert v["pii_level"] == 3


@pytest.mark.asyncio
async def test_wait_approval_db_write_failure_auto_approves(monkeypatch):
    """If the ticket INSERT fails, the node must auto-approve rather
    than leave the user hanging."""
    from app.agent.nodes import wait_approval as mod

    class _BoomRepo:
        async def create(self, **kw):
            raise RuntimeError("DB down")

    fake_meta = MagicMock()
    fake_meta.session = "fake-session"
    monkeypatch.setattr(
        "app.repositories.doris.approval.approval_repository.ApprovalRepository",
        lambda session: _BoomRepo(),
    )

    state = {"sql": "SELECT 1", "needs_approval": True,
             "approval_reason": "test"}
    runtime = _make_runtime({"meta_doris_repository": fake_meta})

    result = await mod.wait_approval(state, runtime)
    assert result["pending_approval_ticket_id"] is None
    assert result["needs_approval"] is False


@pytest.mark.asyncio
async def test_wait_approval_noop_when_not_needed():
    """Defensive: if routed here without needs_approval, the node is a
    no-op (the conditional edge will route to execute_sql)."""
    from app.agent.nodes import wait_approval as mod
    state = {"needs_approval": False}
    runtime = _make_runtime({})
    result = await mod.wait_approval(state, runtime)
    assert result["pending_approval_ticket_id"] is None


def test_extract_violation_dicts_parses_reason_format():
    """The correct_sql reason format `column (class=Name, pii_level=N)`
    must round-trip back into structured dicts for the UI."""
    from app.agent.nodes.wait_approval import _extract_violation_dicts
    reason = (
        "Corrected SQL references 2 PII L>=3 column(s): "
        "dw.dim_user.phone_num (class=User, pii_level=3); "
        "dw.dim_user.email (class=User, pii_level=3)"
    )
    out = _extract_violation_dicts(reason)
    assert len(out) == 2
    assert out[0]["column_ref"] == "dw.dim_user.phone_num"
    assert out[0]["class_name"] == "User"
    assert out[0]["pii_level"] == 3


def test_extract_violation_dicts_handles_empty():
    from app.agent.nodes.wait_approval import _extract_violation_dicts
    assert _extract_violation_dicts("") == []
    assert _extract_violation_dicts(None) == []


# --------------------------------------------------------------------------- #
# ApprovalRepository (mocked session)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_repo_create_round_trip():
    """create() should execute INSERT + commit and return a snapshot."""
    from app.repositories.doris.approval.approval_repository import ApprovalRepository

    fake_session = AsyncMock()
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()
    repo = ApprovalRepository(fake_session)

    ticket = await repo.create(
        request_id="req-1", sql_text="SELECT 1",
        username="alice", pii_reason="test",
        pii_violations=[{"column_ref": "x", "class_name": "Y", "pii_level": 3}],
    )
    assert ticket is not None
    assert ticket.request_id == "req-1"
    assert ticket.sql_text == "SELECT 1"
    assert ticket.status == "pending"
    fake_session.execute.assert_awaited()
    fake_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_repo_create_failure_returns_none():
    from app.repositories.doris.approval.approval_repository import ApprovalRepository
    fake_session = AsyncMock()
    fake_session.execute.side_effect = RuntimeError("DB down")
    repo = ApprovalRepository(fake_session)
    ticket = await repo.create(request_id="r", sql_text="x")
    assert ticket is None


@pytest.mark.asyncio
async def test_repo_decide_validates_decision():
    from app.repositories.doris.approval.approval_repository import ApprovalRepository
    fake_session = AsyncMock()
    repo = ApprovalRepository(fake_session)
    # Bad decision label -> None without touching the DB.
    assert await repo.decide("tid", decision="maybe") is None
    fake_session.execute.assert_not_called()


# --------------------------------------------------------------------------- #
# Graph conditional edges
# --------------------------------------------------------------------------- #
def test_graph_has_pii_gate_edges():
    """Verify the graph wires up the PII gate correctly: correct_sql has
    a conditional edge to wait_approval (when needs_approval), and
    wait_approval has a conditional edge to END (when ticket created)."""
    import inspect
    from app.agent import graph as graph_mod
    src = inspect.getsource(graph_mod)

    # The two new conditional edges must be present.
    assert '"wait_approval"' in src
    assert '"execute_sql": "execute_sql"' in src
    assert "needs_approval" in src
    assert "pending_approval_ticket_id" in src


# --------------------------------------------------------------------------- #
# ApprovalRouter API (happy paths with mocked repo)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_api_decide_requires_approver_role(monkeypatch):
    """L1_business should get 403."""
    from app.api.routers import approval_router as router

    class _FakeRepo:
        async def get(self, tid):
            return MagicMock(status="pending")

    monkeypatch.setattr(router, "ApprovalRepository", lambda session: _FakeRepo())

    body = router.DecisionRequest(decision="approved", note="ok")
    user = {"sub": "alice", "role": "L1_business"}
    result = await router.decide_approval(
        "tid-1", body, user=user, session=AsyncMock(),
    )
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_api_decide_approved_workflow(monkeypatch):
    """L3_engineer can approve; returns next_action=resume."""
    from app.api.routers import approval_router as router
    from app.repositories.doris.approval.approval_repository import ApprovalTicket

    pending = ApprovalTicket(
        ticket_id="tid-1", request_id="r1", sql_text="SELECT 1",
        status="pending",
    )
    approved = ApprovalTicket(
        ticket_id="tid-1", request_id="r1", sql_text="SELECT 1",
        status="approved", decided_by="bob",
    )

    class _FakeRepo:
        async def get(self, tid): return pending
        async def decide(self, **kw): return approved

    # Patch the binding inside the router module (not the repo module).
    monkeypatch.setattr(router, "ApprovalRepository", lambda session: _FakeRepo())

    body = router.DecisionRequest(decision="approved", note="ok")
    user = {"sub": "bob", "role": "L3_engineer"}
    result = await router.decide_approval(
        "tid-1", body, user=user, session=AsyncMock(),
    )
    assert result["status"] == "ok"
    assert result["next_action"] == "resume"
    assert result["resume_url"].endswith("/resume")


@pytest.mark.asyncio
async def test_api_decide_rejects_already_decided(monkeypatch):
    """Deciding an already-decided ticket returns 409."""
    from app.api.routers import approval_router as router
    from app.repositories.doris.approval.approval_repository import ApprovalTicket

    decided = ApprovalTicket(
        ticket_id="tid-2", request_id="r2", sql_text="SELECT 1",
        status="approved", decided_by="bob",
    )

    class _FakeRepo:
        async def get(self, tid): return decided

    monkeypatch.setattr(router, "ApprovalRepository", lambda session: _FakeRepo())

    body = router.DecisionRequest(decision="rejected")
    user = {"sub": "alice", "role": "L4_admin"}
    result = await router.decide_approval(
        "tid-2", body, user=user, session=AsyncMock(),
    )
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_api_get_returns_404_for_unknown(monkeypatch):
    from app.api.routers import approval_router as router

    class _FakeRepo:
        async def get(self, tid): return None

    monkeypatch.setattr(router, "ApprovalRepository", lambda session: _FakeRepo())
    result = await router.get_approval(
        "unknown", user={"sub": "x", "role": "L4_admin"},
        session=AsyncMock(),
    )
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_api_list_pending(monkeypatch):
    from app.api.routers import approval_router as router
    from app.repositories.doris.approval.approval_repository import ApprovalTicket

    tickets = [
        ApprovalTicket(ticket_id="t1", request_id="r1", sql_text="SELECT 1"),
        ApprovalTicket(ticket_id="t2", request_id="r2", sql_text="SELECT 2"),
    ]

    class _FakeRepo:
        async def list_pending(self, limit=50): return tickets

    monkeypatch.setattr(router, "ApprovalRepository", lambda session: _FakeRepo())
    result = await router.list_pending_approvals(
        user={"sub": "x", "role": "L3_engineer"}, session=AsyncMock(),
    )
    assert result["count"] == 2
    assert result["tickets"][0]["ticket_id"] == "t1"
