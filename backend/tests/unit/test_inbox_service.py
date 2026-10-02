"""M3 inbox service tests: routing table, resume-path replication,
multi-round semantics, steer."""
import asyncio
import pytest

from app.services.inbox_service import (
    HandlerResult, _next_node, InboxService,
)


# ------------------------------------------------------------------ #
# _next_node replicates graph.py conditional edges
# ------------------------------------------------------------------ #
def test_next_node_happy_path():
    s = {"error": None, "complexity": "simple", "needs_approval": False}
    assert _next_node("generate_sql", s) == "validate_sql_safety"
    assert _next_node("validate_sql_safety", s) == "validate_sql"
    assert _next_node("validate_sql", s) == "assess_complexity"
    assert _next_node("assess_complexity", s) == "execute_sql"
    assert _next_node("execute_sql", s) == "extract_lineage"
    assert _next_node("code_executor", s) is None


def test_next_node_error_goes_to_correct():
    s = {"error": "syntax error"}
    assert _next_node("validate_sql", s) == "correct_sql"


def test_next_node_complex_goes_to_correct():
    s = {"error": None, "complexity": "complex"}
    assert _next_node("assess_complexity", s) == "correct_sql"


def test_next_node_pii_gate_waits():
    s = {"needs_approval": True}
    assert _next_node("correct_sql", s) == "wait_approval"
    s2 = {"needs_approval": False}
    assert _next_node("correct_sql", s2) == "execute_sql"


def test_next_node_pending_ticket_suspends():
    s = {"pending_approval_ticket_id": "t1"}
    assert _next_node("wait_approval", s) is None
    s2 = {"pending_approval_ticket_id": None}
    assert _next_node("wait_approval", s2) == "execute_sql"


def test_resume_tail_order():
    s = {}
    order = []
    node = "extract_lineage"
    while node:
        order.append(node)
        node = _next_node(node, s)
    assert order == ["extract_lineage", "anomaly_detection",
                     "drill_down_analysis", "decision_insight",
                     "code_executor"]


# ------------------------------------------------------------------ #
# Handler registry
# ------------------------------------------------------------------ #
def test_builtin_handlers_registered():
    from app.services.inbox_service import inbox_service
    kinds = inbox_service.kinds()
    assert "clarify" in kinds and "approval" in kinds


def test_unknown_kind_discarded(monkeypatch):
    """respond on an unregistered kind -> no_handler, item discarded."""
    svc = InboxService()

    class _Repo:
        def __init__(self, session=None): self.calls = []

        async def get(self, iid):
            class _I:
                inbox_id = iid; session_id = "s"; channel = "next_step"
                kind = "mystery"; payload = {}; status = "pending"

                def to_dict(self):
                    return {"inbox_id": iid, "kind": self.kind}
            return _I()

        async def mark(self, iid, st):
            self.calls.append((iid, st))
            return None

    import app.repositories.doris.inbox.inbox_repository as ir_mod
    monkeypatch.setattr(ir_mod, "InboxRepository", _Repo)

    async def run():
        return await svc.respond(None, "ibx_1", {})
    out = asyncio.run(run())
    assert out["status"] == "error"
    assert out["error"] == "no_handler"


def test_approval_handler_role_gate():
    """Deciding via the inbox requires an approver role (fail-closed)."""
    from app.services.inbox_service import _approval_handler

    class _Item:
        session_id = "s"
        payload = {"ref_id": "tkt_1"}

    def run(role):
        return asyncio.run(_approval_handler(_Item(), {"decision": "approved"},
                                             None, username="u1", role=role))

    out = run("L1_analyst")
    assert out.outcome == "forbidden" and out.resume_node is None
    out2 = run("L4_admin")
    # L4 passes the gate; proceeds to ticket lookup (repo None -> error).
    assert out2.outcome != "forbidden"


def test_handler_result_shapes():
    r = HandlerResult(resume_node="execute_sql", sse_stream=True)
    assert r.sse_stream and r.resume_node == "execute_sql"
    r2 = HandlerResult(resume_node=None, outcome="amended")
    assert not r2.sse_stream and r2.outcome == "amended"
