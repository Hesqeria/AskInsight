"""LLM call retry + timeout wrapper (DB-GPT#669 model loading crash).

M5: execution moved to app/agent/llm_adapter.py (declarative policy
from app_config.llm.retry, llm/retry events, usage calibration). These
wrappers keep the historical safe_ainvoke / safe_ainvoke_chain entry
points every node already uses - explicit retries/timeout args override
the declared policy.
"""
import asyncio

from app.core.log import logger


async def safe_ainvoke(llm, messages, retries: int = None,
                       timeout: int = None):
    """LLM call with timeout + retry (policy-declared, event-logged).

    Args:
        llm: LLM instance (ChatOpenAI) or LLMAdapter
        messages: message list
        retries: override max retry count (None = config policy)
        timeout: override timeout seconds (None = config policy)
    """
    from app.agent.llm_adapter import LLMAdapter, LangChainAdapter, \
        RetryPolicy, ainvoke_with_policy
    adapter = llm if isinstance(llm, LLMAdapter) else LangChainAdapter(llm)
    policy = adapter.retry_policy()
    if retries is not None or timeout is not None:
        policy = RetryPolicy(
            retries=policy.retries if retries is None else retries,
            timeout=policy.timeout if timeout is None else timeout,
            backoff_base=policy.backoff_base,
            backoff_jitter=policy.backoff_jitter)
    return await ainvoke_with_policy(adapter, messages, policy)


async def safe_ainvoke_chain(chain, input_data, retries: int = None,
                             timeout: int = None):
    """LangChain chain call with timeout + retry (same policy)."""
    from app.agent.llm_adapter import RetryPolicy
    policy = RetryPolicy.from_config()
    retries = policy.retries if retries is None else retries
    timeout = policy.timeout if timeout is None else timeout
    last_error = None
    for attempt in range(retries + 1):
        try:
            result = await asyncio.wait_for(
                chain.ainvoke(input_data), timeout=timeout
            )
            return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Chain failed (attempt {attempt+1}): {last_error[:80]}")
        if attempt < retries:
            from app.agent.llm_adapter import _emit_retry
            _emit_retry(attempt, last_error, policy)
            await asyncio.sleep(policy.backoff_seconds(attempt))
    raise RuntimeError(f"Chain call failed (retried {retries} times): {last_error}")
