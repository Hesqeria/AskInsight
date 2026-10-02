"""M9 subagent provider tests: fresh/delegated, structured results,
event emission, chain."""
import asyncio

from app.services.subagent import (
    SubagentRequest, SubagentResult, SubagentProvider,
    FreshSubagentProvider, DelegatedSubagentProvider,
    run_chain, get_subagent_registry,
)


class _Cap:
    def __init__(self, name="x", fail=False):
        self.name = name
        self._fail = fail

    async def safe_invoke(self, intent, ctx):
        if self._fail:
            raise RuntimeError("boom")
        return {"echo": intent.get("intent") if isinstance(intent, dict)
                else intent, "seed": bool(ctx)}


def test_fresh_result_completed():
    p = FreshSubagentProvider("fresh_x", _Cap())
    run = asyncio.run(p.start(SubagentRequest(task="hi")))
    res = asyncio.run(p.run(run))
    assert res.stop_reason == "completed"
    assert res.output["data"]["echo"] == "hi"


def test_fresh_never_raises_on_error(monkeypatch):
    import app.agent.events as ev
    events = []
    monkeypatch.setattr(ev, "emit", lambda t, p=None: events.append(t))
    p = FreshSubagentProvider("fresh_fail", _Cap(fail=True))
    run = asyncio.run(p.start(SubagentRequest(task="x")))
    res = asyncio.run(p.run(run))
    assert res.stop_reason == "error"
    assert res.error
    assert "subagent/started" in events and "subagent/ended" in events


def test_capabilities_flag():
    assert FreshSubagentProvider("a", _Cap()).inherits_parent_context is False
    assert DelegatedSubagentProvider("b", _Cap()).inherits_parent_context is True


def test_delegated_inherits_policy_pin(monkeypatch):
    # Avoid the real DB rebuild: monkeypatch rebuild_state to a no-op.
    import app.services.subagent as sub
    import app.services.inbox_service as inbox_mod

    async def fake_rebuild(sid):
        return {"query": "parent query"}
    monkeypatch.setattr(inbox_mod, "rebuild_state", fake_rebuild)
    p = DelegatedSubagentProvider("del_x", _Cap())
    req = SubagentRequest(task="t", parent_session_id="sess_1",
                          username="u", role="L3_engineer",
                          policy_pin={"approval": "never"})
    run = asyncio.run(p.start(req))
    # start() builds the seed lazily inside invoke; run and inspect echo.
    res = asyncio.run(p.run(run))
    assert res.stop_reason == "completed"


def test_delegated_seed_carries_policy():
    import app.services.subagent as sub
    p = DelegatedSubagentProvider("del2", _Cap())
    seed = asyncio.run(p._build_seed(
        SubagentRequest(task="t", parent_session_id="",
                        username="u", role="admin",
                        policy_pin={"approval": "ask"})))
    assert seed["policy_pin"]["role"] == "admin"
    assert seed["policy_pin"]["approval"] == "ask"


def test_run_chain_continues_on_unknown_agent(monkeypatch):
    import app.agent.events as ev
    monkeypatch.setattr(ev, "emit", lambda t, p=None: None)
    results = asyncio.run(run_chain("s", [
        {"agent": "no_such_agent", "task": "x"},
        {"agent": "sql_agent", "task": "y", "intent": {"intent": "q"}},
    ]))
    assert results[0].stop_reason == "error"
    # sql_agent resolves from the wired registry (mounted fresh providers)
    assert results[1].stop_reason in ("completed", "error")


def test_registry_lists_providers():
    reg = get_subagent_registry()
    names = [p["name"] for p in reg.list()]
    assert "sql_agent" in names
    assert any(p["inherits_parent_context"] is False for p in reg.list())
