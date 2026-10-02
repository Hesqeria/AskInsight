"""Subagent providers (PRD M9 delegated turn).

dsh subagents insight: fresh (zero context) vs delegated (parent's
completed-turn prefix as seed; policy inherited with the seed).

- SubagentProvider abstraction (FR1): start(request) -> SubagentRun;
  capabilities() declares inherits_parent_context.
- FreshProvider (FR2): wraps an existing orchestrator AgentCapability
  (zero parent context). Mounts the six wired agents.
- DelegatedProvider (FR3): rebuilds the seed from the parent session's
  M1 event prefix (up to the last turn/end) and inherits the policy pin
  (user role / RBAC / approval) with it.
- Results are structured and NEVER raise (FR4): failures come back as
  {stop_reason: 'error', error} - a failed child is data, not a crash.
- Every run emits subagent/started|ended into the M1 log (FR5).
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.core.log import logger


@dataclass
class SubagentRequest:
    task: str = ""
    intent: dict = field(default_factory=dict)
    parent_session_id: str = ""          # delegated seed source
    username: str = "anonymous"
    role: str = "user"
    # Inherited policy pin (delegated): user role / RBAC / approval mode.
    policy_pin: dict = field(default_factory=dict)


@dataclass
class SubagentResult:
    stop_reason: str = "completed"       # completed | error | cancelled
    output: dict = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return {"stop_reason": self.stop_reason, "output": self.output,
                "error": self.error}


class SubagentRun:
    """A started run handle (result + dispose, FR1)."""

    def __init__(self, provider: "SubagentProvider", request: SubagentRequest,
                 invoke: Callable):
        self.provider = provider
        self.request = request
        self._invoke = invoke
        self.run_id = uuid.uuid4().hex[:12]

    async def dispose(self) -> None:
        # Hook for resources (sandbox/e2b worlds) - no-op by default.
        pass


class SubagentProvider:
    name = "base"
    inherits_parent_context = False

    def capabilities(self) -> dict:
        return {"name": self.name,
                "inherits_parent_context": self.inherits_parent_context}

    async def start(self, request: SubagentRequest) -> SubagentRun:
        raise NotImplementedError

    async def run(self, run: SubagentRun) -> SubagentResult:
        """Structured result; NEVER raises (FR4)."""
        _emit("subagent/started", {"subagent": self.name,
                                   "run_id": run.run_id,
                                   "inherits_parent_context":
                                       self.inherits_parent_context,
                                   "task": run.request.task[:200]})
        try:
            output = await run._invoke(run.request)
            result = SubagentResult(stop_reason="completed", output=output)
        except asyncio.CancelledError:
            result = SubagentResult(stop_reason="cancelled",
                                    error="cancelled")
        except Exception as e:
            logger.warning(f"subagent {self.name} failed: {e}")
            result = SubagentResult(stop_reason="error", error=str(e)[:300])
        _emit("subagent/ended", {"subagent": self.name,
                                 "run_id": run.run_id,
                                 "stop_reason": result.stop_reason})
        return result


class FreshSubagentProvider(SubagentProvider):
    """Zero-context child: wraps an existing AgentCapability (FR2)."""

    inherits_parent_context = False

    def __init__(self, name: str, capability):
        self.name = name
        self._cap = capability

    async def start(self, request: SubagentRequest) -> SubagentRun:
        async def invoke(req: SubagentRequest):
            out = await self._cap.safe_invoke(req.intent or
                                              {"intent": req.task}, None)
            if hasattr(out, "to_dict"):
                return out.to_dict()
            return {"data": out}

        return SubagentRun(self, request, invoke)


class DelegatedSubagentProvider(SubagentProvider):
    """Delegated turn: seed = parent session's M1 event prefix; policy
    inherited with the seed (FR3)."""

    inherits_parent_context = True

    def __init__(self, name: str, capability):
        self.name = name
        self._cap = capability

    async def _build_seed(self, request: SubagentRequest) -> dict:
        """Rebuild parent context from the M1 event log (best-effort)."""
        seed: dict = {"parent_session_id": request.parent_session_id,
                      "task": request.task}
        if request.parent_session_id:
            try:
                from app.services.inbox_service import rebuild_state
                state = await rebuild_state(request.parent_session_id)
                if state:
                    seed["parent_context"] = {
                        k: v for k, v in state.items()
                        if not str(k).startswith("_")}
            except Exception as e:
                logger.debug(f"delegated seed rebuild failed: {e}")
        # Policy inheritance (user role / RBAC / approval pin).
        seed["policy_pin"] = dict(request.policy_pin or {})
        seed["policy_pin"].setdefault("role", request.role)
        seed["policy_pin"].setdefault("username", request.username)
        return seed

    async def start(self, request: SubagentRequest) -> SubagentRun:
        seed = await self._build_seed(request)

        async def invoke(req: SubagentRequest):
            out = await self._cap.safe_invoke(req.intent or
                                              {"intent": req.task}, seed)
            if hasattr(out, "to_dict"):
                return out.to_dict()
            return {"data": out}

        return SubagentRun(self, request, invoke)


# --------------------------------------------------------------------- #
# Registry + built-in providers (mounting the six wired agents, FR2)
# --------------------------------------------------------------------- #
class SubagentProviderRegistry:
    def __init__(self):
        self._providers: dict[str, SubagentProvider] = {}

    def register(self, provider: SubagentProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> Optional[SubagentProvider]:
        return self._providers.get(name)

    def list(self) -> list[dict]:
        return [p.capabilities() for p in self._providers.values()]


_registry: SubagentProviderRegistry | None = None


def get_subagent_registry() -> SubagentProviderRegistry:
    global _registry
    if _registry is None:
        _registry = SubagentProviderRegistry()
        _mount_builtin(_registry)
    return _registry


def _mount_builtin(reg: SubagentProviderRegistry) -> None:
    """Mount the six orchestrator agents as FRESH providers (FR2)."""
    try:
        from app.orchestrator.factory import get_wired_registry
        cap_reg = get_wired_registry()
        for cap in cap_reg._capabilities.values():
            reg.register(FreshSubagentProvider(cap.name, cap))
    except Exception as e:
        logger.warning(f"subagent fresh mount failed: {e}")


def delegated(name: str, capability) -> DelegatedSubagentProvider:
    """Create a delegated provider for a capability (FR3)."""
    return DelegatedSubagentProvider(name, capability)
async def run_chain(parent_session_id: str, steps: list[dict],
                    username: str = "anonymous", role: str = "user",
                    policy_pin: dict = None) -> list[SubagentResult]:
    """Delegated chain (query -> root cause -> report...): each step is
    a delegated subagent whose seed inherits the parent prefix + policy.
    A step that errors still yields a structured result and the chain
    continues (FR4)."""
    reg = get_subagent_registry()
    results = []
    ctx = {}
    for step in steps:
        name = step.get("agent", "")
        provider = reg.get(name)
        if provider is None:
            results.append(SubagentResult(stop_reason="error",
                                          error=f"unknown agent {name}"))
            continue
        request = SubagentRequest(
            task=step.get("task", ""),
            intent=step.get("intent", {}),
            parent_session_id=parent_session_id,
            username=username, role=role,
            policy_pin=dict(policy_pin or {}) or {"role": role},
        )
        request.intent = {**request.intent, "_chain_context": ctx}
        run = await provider.start(request)
        result = await provider.run(run)
        results.append(result)
        ctx = {**ctx, **result.output}
        await run.dispose()
    return results


def _emit(type_: str, payload: dict) -> None:
    try:
        from app.agent.events import emit
        emit(type_, payload)
    except Exception:
        pass
