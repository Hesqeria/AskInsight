"""P1-08: Agent registry - capability-aware agent lookup for multi-agent orchestration."""
import time


class AgentCapability:
    """Capability descriptor + safe invocation wrapper for an agent."""

    def __init__(self, name, description, intents, invoke, role="L2_analyst",
                 max_retries=2):
        self.name = name
        self.description = description
        self.intents = intents
        self._invoke = invoke
        self.role = role
        self.max_retries = max_retries
        self.stats = {"calls": 0, "failures": 0, "latency_sum_ms": 0.0}
        self.circuit_break_until = 0.0

    async def safe_invoke(self, intent, ctx):
        from datetime import datetime
        if time.time() < self.circuit_break_until:
            raise RuntimeError(f"agent {self.name} circuit-broken")
        start = time.time()
        self.stats["calls"] += 1
        for attempt in range(self.max_retries + 1):
            try:
                result = await self._invoke(intent, ctx)
                self.stats["latency_sum_ms"] += (time.time() - start) * 1000
                return result
            except Exception:
                if attempt == self.max_retries:
                    self.stats["failures"] += 1
                    raise
        return None

    def health(self):
        calls = self.stats["calls"]
        if calls == 0:
            return {"healthy": True, "success_rate": 1.0, "avg_latency_ms": 0.0}
        success_rate = 1.0 - self.stats["failures"] / calls
        avg_latency = self.stats["latency_sum_ms"] / calls
        healthy = success_rate > 0.8 and avg_latency < 10000
        return {"healthy": healthy, "success_rate": round(success_rate, 3),
                "avg_latency_ms": round(avg_latency, 1)}


class AgentRegistry:
    """Registry of agent capabilities for intent-based routing."""

    def __init__(self):
        self._capabilities = {}

    def register(self, capability: AgentCapability):
        self._capabilities[capability.name] = capability

    def find_by_intent(self, intent: str) -> AgentCapability:
        for cap in self._capabilities.values():
            if intent in cap.intents:
                return cap
        return self._capabilities.get("intent_agent")

    def get(self, name: str) -> AgentCapability:
        return self._capabilities.get(name)

    def list(self) -> list:
        return [{"name": c.name, "description": c.description, "intents": c.intents,
                 "role": c.role} for c in self._capabilities.values()]

    def circuit_break(self, name, seconds=300):
        cap = self._capabilities.get(name)
        if cap:
            cap.circuit_break_until = time.time() + seconds


_registry = None


def get_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry
