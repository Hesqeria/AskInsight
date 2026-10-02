"""LLM adapter seam + declarative retry (PRD M5).

dsh separation, Python style:
- Adapter only TRANSLATES: LangChainAdapter wraps the existing
  ChatOpenAI runnable behind a narrow interface (ainvoke / stream /
  provider_info / retry_policy) - FR1. Swapping providers = registering
  another adapter; every consumer keeps calling safe_ainvoke.
- Retry policy is DECLARED in config (app_config.llm.retry, FR2) and
  EXECUTED here - not sprinkled through node code.
- Every retry is event-logged BEFORE the wait (dsh rule: 重试计数跨崩溃
  持久) as llm/retry - FR3.
- Successful responses calibrate the M8 TokenMeter from real usage
  numbers (FR4).
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

from app.core.log import logger


@dataclass
class RetryPolicy:
    retries: int = 2
    timeout: int = 90
    backoff_base: float = 2.0
    backoff_jitter: bool = True

    @classmethod
    def from_config(cls) -> "RetryPolicy":
        try:
            from app.conf.app_config import app_config
            r = app_config.llm.retry
            return cls(retries=r.retries, timeout=r.timeout,
                       backoff_base=r.backoff_base,
                       backoff_jitter=r.backoff_jitter)
        except Exception:
            return cls()

    def backoff_seconds(self, attempt: int) -> float:
        delay = self.backoff_base * (attempt + 1)
        if self.backoff_jitter:
            delay *= 0.5 + random.random()
        return delay


class LLMAdapter:
    """Narrow seam every LLM consumer depends on (FR1)."""

    name: str = "base"

    async def ainvoke(self, messages):
        raise NotImplementedError

    def stream(self, messages):  # optional (dsh: stream() required)
        raise NotImplementedError

    def provider_info(self) -> dict:
        return {"adapter": self.name}

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy.from_config()


class LangChainAdapter(LLMAdapter):
    """Wraps the existing ChatOpenAI runnable (12-provider parity comes
    from llm_provider.build_llm; the adapter is transport-neutral)."""

    def __init__(self, runnable, name: str = "langchain-openai",
                 model: str = ""):
        self._runnable = runnable
        self.name = name
        self._model = model

    @property
    def runnable(self):
        return self._runnable

    async def ainvoke(self, messages):
        return await self._runnable.ainvoke(messages)

    def stream(self, messages):
        return self._runnable.stream(messages)

    def provider_info(self) -> dict:
        return {"adapter": self.name, "model": self._model}

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy.from_config()


_default_adapter: LLMAdapter | None = None


def get_adapter() -> LLMAdapter:
    """Process-wide default adapter (wraps app.agent.llm.llm lazily)."""
    global _default_adapter
    if _default_adapter is None:
        from app.agent.llm import llm
        from app.conf.app_config import app_config
        _default_adapter = LangChainAdapter(
            llm, model=app_config.llm.model_name)
    return _default_adapter


def register_adapter(adapter: LLMAdapter) -> None:
    """Atomic swap of the default adapter (dsh registerAdapter)."""
    global _default_adapter
    _default_adapter = adapter


# --------------------------------------------------------------------- #
# Policy execution + events + usage calibration
# --------------------------------------------------------------------- #
def _emit_retry(attempt: int, error: str, policy: RetryPolicy) -> None:
    """llm/retry BEFORE the backoff wait (FR3, crash-persistent count)."""
    try:
        from app.agent.events import emit
        emit("llm/retry", {"attempt": attempt + 1,
                           "max_retries": policy.retries,
                           "error": str(error)[:200]})
    except Exception:
        pass


def _calibrate_usage(messages, response) -> None:
    """FR4: real usage -> TokenMeter calibration (best-effort)."""
    try:
        usage = getattr(response, "usage_metadata", None) or {}
        input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
        if not input_tokens:
            meta = getattr(response, "response_metadata", None) or {}
            tu = (meta.get("token_usage") or meta.get("usage") or {})
            input_tokens = tu.get("prompt_tokens")
        if not input_tokens:
            return
        estimated = 0
        from app.core.token_meter import estimate_tokens, calibrate
        for m in messages:
            content = getattr(m, "content", m)
            estimated += estimate_tokens(str(content))
        if estimated > 0:
            calibrate(estimated, int(input_tokens))
    except Exception:
        pass


async def ainvoke_with_policy(adapter: LLMAdapter, messages,
                              policy: RetryPolicy = None):
    """Invoke with declared retries/timeout/backoff; per-retry events;
    usage calibration on success. Raises RuntimeError after exhausting."""
    policy = policy or adapter.retry_policy()
    last_error = None
    for attempt in range(policy.retries + 1):
        try:
            result = await asyncio.wait_for(
                adapter.ainvoke(messages), timeout=policy.timeout)
            _calibrate_usage(messages, result)
            return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_error = str(e)
            logger.warning(
                f"LLM call failed (attempt {attempt + 1}/"
                f"{policy.retries + 1}): {last_error[:120]}")
            if attempt < policy.retries:
                _emit_retry(attempt, last_error, policy)
                await asyncio.sleep(policy.backoff_seconds(attempt))
    raise RuntimeError(
        f"LLM call failed (retried {policy.retries} times): {last_error}")
