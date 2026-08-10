"""P1-08 Multi-agent orchestrator tests."""
import asyncio

from app.orchestrator.agent_registry import AgentRegistry, AgentCapability
from app.orchestrator.multi_agent import (
    MultiAgentOrchestrator, AgentResult, AgentContext, CollaborationMode,
)


def _mk_result(agent, intent, data=None, artifacts=None, ok=True):
    return AgentResult(ok=ok, agent=agent, intent=intent, data=data or {},
                       artifacts=artifacts or {})


def _build_registry():
    reg = AgentRegistry()
    async def sql_invoke(intent, ctx):
        return _mk_result("sql_agent", "data_query", {"sql": "SELECT 1", "severity": "info"})
    async def gov_invoke(intent, ctx):
        return _mk_result("governance_agent", "quality_check",
                          {"quality": "normal", "severity": "info"},
                          artifacts={"agent_type": "data_quality"})
    async def root_cause_invoke(intent, ctx):
        return _mk_result("alert_root_cause_agent", "alert_investigate",
                          {"root_cause": "dag failed", "severity": "critical"})
    async def metric_invoke(intent, ctx):
        return _mk_result("metric_agent", "metric_define", {"metric": "GMV"})
    async def etl_invoke(intent, ctx):
        return _mk_result("etl_agent", "etl_request", {"model": "dws_x"})
    async def intent_invoke(intent, ctx):
        return _mk_result("intent_agent", "metadata_query", {"answer": "ok"})

    reg.register(AgentCapability("sql_agent", "SQL gen", ["data_query"], sql_invoke))
    reg.register(AgentCapability("governance_agent", "governance", ["quality_check", "anomaly_explain"], gov_invoke))
    reg.register(AgentCapability("alert_root_cause_agent", "root cause", ["alert_investigate", "anomaly_explain"], root_cause_invoke))
    reg.register(AgentCapability("metric_agent", "metric", ["metric_define"], metric_invoke))
    reg.register(AgentCapability("etl_agent", "etl", ["etl_request"], etl_invoke))
    reg.register(AgentCapability("intent_agent", "intent", ["metadata_query"], intent_invoke))
    return reg


def test_dispatch_route_data_query():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    d = orch.dispatch({"query_id": "q1"}, {"intent": "data_query"})
    assert d.primary_agent == "sql_agent"
    assert d.mode == CollaborationMode.SINGLE


def test_dispatch_route_anomaly_parallel():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    d = orch.dispatch({"query_id": "q2"}, {"intent": "anomaly_explain"})
    assert d.primary_agent == "governance_agent"
    assert d.mode == CollaborationMode.PARALLEL
    assert "alert_root_cause_agent" in d.supporting_agents


def test_dispatch_route_etl_sequential():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    d = orch.dispatch({"query_id": "q3"}, {"intent": "etl_request"})
    assert d.mode == CollaborationMode.SEQUENTIAL
    assert d.primary_agent == "etl_agent"
    assert "sql_agent" in d.supporting_agents


def test_execute_single_mode():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    d = orch.dispatch({"query_id": "q1"}, {"intent": "data_query"})
    res = asyncio.run(orch.execute_dispatch(d, {"query_id": "q1"}, {"intent": "data_query"}))
    assert res.ok is True
    assert res.data["sql"] == "SELECT 1"


def test_execute_parallel_merges():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    d = orch.dispatch({"query_id": "q2"}, {"intent": "anomaly_explain"})
    res = asyncio.run(orch.execute_dispatch(d, {"query_id": "q2"}, {"intent": "anomaly_explain"}))
    assert res.ok is True
    assert res.data.get("quality") == "normal"
    assert res.data.get("root_cause") == "dag failed"


def test_execute_sequential_passes_context():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    d = orch.dispatch({"query_id": "q3"}, {"intent": "etl_request"})
    res = asyncio.run(orch.execute_dispatch(d, {"query_id": "q3"}, {"intent": "etl_request"}))
    assert res.ok is True
    assert res.data.get("model") == "dws_x"


def test_arbitrate_data_layer_first():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    gov = _mk_result("governance_agent", "quality_check", {"quality": "normal"},
                     artifacts={"agent_type": "data_quality"})
    rca = _mk_result("alert_root_cause_agent", "alert_investigate", {"root_cause": "system"})
    winner = asyncio.run(orch.arbitrate([rca, gov], "table_delay"))
    assert winner.agent == "governance_agent"


def test_arbitrate_highest_severity():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    info = _mk_result("a1", "x", {"severity": "info"})
    crit = _mk_result("a2", "x", {"severity": "critical"})
    winner = asyncio.run(orch.arbitrate([info, crit], "metric_dispute"))
    assert winner.agent == "a2"


def test_arbitrate_unknown_scenario_escalates():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    r = asyncio.run(orch.arbitrate([_mk_result("a1", "x")], "unknown_scenario"))
    assert r.ok is False
    assert r.data.get("escalated") is True
    assert "ticket" in r.data.get("ticket_id", "")


def test_escalate_creates_ticket():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    ticket = asyncio.run(orch.escalate("agent failed", AgentContext(request_id="r1"),
                                       artifacts={"sql": "..."}))
    assert ticket.startswith("ticket_")


def test_agent_health_and_circuit_break():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)

    async def flaky(intent, ctx):
        raise RuntimeError("boom")

    reg.register(AgentCapability("flaky_agent", "flaky", ["x"], flaky))
    # invoke flaky agent to drive its failure stats up
    cap = reg.get("flaky_agent")
    async def invoke_once():
        try:
            await cap.safe_invoke({"intent": "x"}, AgentContext(request_id="r"))
        except Exception:
            pass
    for _ in range(5):
        asyncio.run(invoke_once())
    h = asyncio.run(orch.get_agent_health())
    assert h["flaky_agent"]["healthy"] is False
    # circuit broken -> safe_invoke raises immediately
    try:
        asyncio.run(cap.safe_invoke({"intent": "x"}, AgentContext(request_id="r2")))
        assert False, "expected circuit-broken exception"
    except RuntimeError as e:
        assert "circuit-broken" in str(e)


def test_dispatch_fallback_agent():
    reg = _build_registry()
    orch = MultiAgentOrchestrator(reg)
    d = orch.dispatch({"query_id": "q9"}, {"intent": "data_query"})
    assert d.fallback_agent == "intent_agent"
