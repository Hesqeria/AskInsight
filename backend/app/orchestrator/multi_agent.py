"""P1-08: Multi-agent orchestration hub - dispatch, execute, arbitrate, escalate, health."""
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field


class CollaborationMode:
    SINGLE = "single"
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"


@dataclass
class AgentDispatch:
    request_id: str
    mode: str = CollaborationMode.SINGLE
    primary_agent: str = ""
    supporting_agents: list = field(default_factory=list)
    shared_context_keys: list = field(default_factory=list)
    fallback_agent: str = ""

    def to_dict(self):
        return {"request_id": self.request_id, "mode": self.mode,
                "primary_agent": self.primary_agent,
                "supporting_agents": self.supporting_agents,
                "shared_context_keys": self.shared_context_keys,
                "fallback_agent": self.fallback_agent}


@dataclass
class AgentResult:
    ok: bool = True
    agent: str = ""
    intent: str = ""
    data: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)
    error: str = ""

    def to_dict(self):
        return {"ok": self.ok, "agent": self.agent, "intent": self.intent,
                "data": self.data, "artifacts": self.artifacts, "error": self.error}


_INTENT_ROUTE = {
    "data_query":      {"primary": "sql_agent", "supporting": [],
                        "mode": CollaborationMode.SINGLE, "fallback": "intent_agent"},
    "anomaly_explain": {"primary": "governance_agent", "supporting": ["alert_root_cause_agent"],
                        "mode": CollaborationMode.PARALLEL, "fallback": "intent_agent"},
    "metric_define":   {"primary": "metric_agent", "supporting": [],
                        "mode": CollaborationMode.SINGLE, "fallback": "intent_agent"},
    "metadata_query":  {"primary": "intent_agent", "supporting": [],
                        "mode": CollaborationMode.SINGLE, "fallback": "intent_agent"},
    "quality_check":   {"primary": "governance_agent", "supporting": [],
                        "mode": CollaborationMode.SINGLE, "fallback": "intent_agent"},
    "etl_request":     {"primary": "etl_agent", "supporting": ["sql_agent"],
                        "mode": CollaborationMode.SEQUENTIAL, "fallback": "intent_agent"},
    "alert_investigate": {"primary": "alert_root_cause_agent", "supporting": ["governance_agent"],
                          "mode": CollaborationMode.PARALLEL, "fallback": "intent_agent"},
}

_ARBITRATION_RULES = {
    "table_delay": {
        "agents_in_conflict": ["governance_agent", "alert_root_cause_agent"],
        "arbitration_logic": "data_layer_first",
    },
    "metric_dispute": {
        "agents_in_conflict": ["governance_agent"],
        "arbitration_logic": "highest_severity",
    },
}


@dataclass
class AgentContext:
    request_id: str
    user_id: str = ""
    user_role: str = "L2_analyst"
    session_id: str = ""


class MultiAgentOrchestrator:
    def __init__(self, registry, memory=None, notifier=None, approval=None):
        self.registry = registry
        self.memory = memory
        self.notifier = notifier
        self.approval = approval

    def dispatch(self, request, intent) -> AgentDispatch:
        intent_name = intent.get("intent", intent) if isinstance(intent, dict) else str(intent)
        route = _INTENT_ROUTE.get(intent_name, _INTENT_ROUTE["data_query"])
        req_id = request.get("query_id") if isinstance(request, dict) else getattr(
            request, "query_id", uuid.uuid4().hex)
        return AgentDispatch(
            request_id=req_id, mode=route["mode"], primary_agent=route["primary"],
            supporting_agents=list(route["supporting"]),
            shared_context_keys=["query", "intent"],
            fallback_agent=route["fallback"],
        )

    async def execute_dispatch(self, dispatch: AgentDispatch, request, intent) -> AgentResult:
        ctx = self._build_context(dispatch.request_id, request)
        primary = self.registry.get(dispatch.primary_agent)
        if primary is None:
            return AgentResult(ok=False, agent=dispatch.primary_agent,
                               error="agent not registered")

        if dispatch.mode == CollaborationMode.PARALLEL:
            names = [dispatch.primary_agent] + dispatch.supporting_agents
            tasks = []
            for name in names:
                cap = self.registry.get(name)
                if cap is not None:
                    tasks.append(cap.safe_invoke(intent, ctx))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            merged = AgentResult(ok=True, agent=dispatch.primary_agent, intent=str(intent))
            for r in results:
                if isinstance(r, AgentResult):
                    merged.data.update(r.data)
                    if not r.ok:
                        merged.ok = False
            return merged

        if dispatch.mode == CollaborationMode.SEQUENTIAL:
            primary_result = await primary.safe_invoke(intent, ctx)
            if self.memory is not None:
                self.memory.set(dispatch.request_id, "primary_result",
                                primary_result.data if isinstance(primary_result, AgentResult) else {})
            supporting_result = None
            for name in dispatch.supporting_agents:
                cap = self.registry.get(name)
                if cap is not None:
                    supporting_result = await cap.safe_invoke(
                        primary_result.data if isinstance(primary_result, AgentResult) else {},
                        ctx)
            merged = AgentResult(ok=True, agent=dispatch.primary_agent, intent=str(intent))
            if isinstance(primary_result, AgentResult):
                merged.data.update(primary_result.data)
            if isinstance(supporting_result, AgentResult):
                merged.data.update(supporting_result.data)
            return merged

        result = await primary.safe_invoke(intent, ctx)
        if isinstance(result, AgentResult):
            return result
        return AgentResult(ok=False, agent=dispatch.primary_agent, data={},
                           error="invalid agent result")

    async def arbitrate(self, conflicts: list, scenario: str) -> AgentResult:
        rule = _ARBITRATION_RULES.get(scenario)
        if rule is None:
            return await self.escalate_to_human(conflicts, scenario)
        logic = rule["arbitration_logic"]
        if logic == "data_layer_first":
            for r in conflicts:
                if isinstance(r, AgentResult) and r.artifacts.get("agent_type") == "data_quality":
                    return r
            return conflicts[0]
        if logic == "highest_severity":
            ordered = sorted(conflicts, key=_severity_rank, reverse=True)
            return ordered[0]
        return await self.escalate_to_human(conflicts, scenario)

    async def escalate(self, reason, context, artifacts=None) -> str:
        ticket_id = "ticket_" + uuid.uuid4().hex[:12]
        if self.notifier is not None:
            try:
                await self.notifier.notify(
                    "dingtalk", self._get_oncall(), "Agent escalation: " + reason,
                    json.dumps(artifacts or {}, ensure_ascii=False), severity="P0")
            except Exception:
                pass
        return ticket_id

    async def escalate_to_human(self, conflicts, scenario) -> AgentResult:
        await self.escalate("conflict arbitration needed: " + scenario,
                            AgentContext(request_id="ctx"), {})
        return AgentResult(ok=False, agent="human", intent=scenario,
                           data={"escalated": True,
                                 "ticket_id": "ticket_" + uuid.uuid4().hex[:12]})

    async def get_agent_health(self) -> dict:
        result = {}
        for name in list(self.registry._capabilities.keys()):
            cap = self.registry._capabilities[name]
            h = cap.health()
            result[name] = h
            if not h["healthy"]:
                self.registry.circuit_break(name, seconds=300)
        return result

    def _build_context(self, request_id, request):
        if isinstance(request, dict):
            return AgentContext(request_id=request_id,
                                user_id=request.get("user_id", ""),
                                user_role=request.get("role", "L2_analyst"),
                                session_id=request.get("session_id", ""))
        return AgentContext(request_id=request_id)

    def _get_oncall(self):
        return ["oncall-eng@corp.com"]


def _severity_rank(result):
    if not isinstance(result, AgentResult):
        return 0
    sev = result.data.get("severity", "info")
    return {"critical": 3, "warning": 2, "info": 1}.get(sev, 0)


_orchestrator = None


def get_orchestrator() -> MultiAgentOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        from app.orchestrator.agent_registry import get_registry
        _orchestrator = MultiAgentOrchestrator(registry=get_registry())
    return _orchestrator
