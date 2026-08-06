"""LLM call retry + timeout wrapper (DB-GPT#669 model loading crash).

Usage:
  from app.core.llm_retry import safe_ainvoke
  result = await safe_ainvoke(llm, [HumanMessage(content="...")])
"""
import asyncio
from app.core.log import logger

MAX_RETRIES = 2
TIMEOUT_SECONDS = 90


async def safe_ainvoke(llm, messages, retries: int = MAX_RETRIES, timeout: int = TIMEOUT_SECONDS):
    """LLM call with timeout + retry.

    Args:
        llm: LLM instance (e.g. ChatOpenAI)
        messages: message list
        retries: max retry count
        timeout: timeout in seconds

    Returns:
        LLM response
    """
    last_error = None
    for attempt in range(retries + 1):
        try:
            result = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=timeout
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"LLM timeout (attempt {attempt+1}/{retries+1}, {timeout}s)")
            last_error = f"LLM timeout ({timeout}s)"
        except Exception as e:
            logger.warning(f"LLM call failed (attempt {attempt+1}): {str(e)[:80]}")
            last_error = str(e)
        if attempt < retries:
            await asyncio.sleep(2 * (attempt + 1))  # Incremental backoff

    raise RuntimeError(f"LLM call failed (retried {retries} times): {last_error}")


async def safe_ainvoke_chain(chain, input_data, retries: int = MAX_RETRIES, timeout: int = TIMEOUT_SECONDS):
    """LangChain chain call with timeout + retry."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            result = await asyncio.wait_for(
                chain.ainvoke(input_data),
                timeout=timeout
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Chain timeout (attempt {attempt+1})")
            last_error = f"Timeout ({timeout}s)"
        except Exception as e:
            logger.warning(f"Chain failed (attempt {attempt+1}): {str(e)[:80]}")
            last_error = str(e)
        if attempt < retries:
            await asyncio.sleep(2 * (attempt + 1))

    raise RuntimeError(f"Chain call failed (retried {retries} times): {last_error}")
