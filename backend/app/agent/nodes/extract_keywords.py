import jieba.analyse
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def extract_keywords(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Extract Keywords"})
    try:
        query = state.get("query") or ""
        # B4.1 fallback: do not raise on empty query
        # C1: async jieba (avoid CPU-intensive blocking of the event loop)
        import asyncio
        if query:
            keywords = await asyncio.to_thread(jieba.analyse.extract_tags, query)
        else:
            keywords = []
        keywords = list(set(keywords + [query]))
        logger.info(f"Keywords: {keywords}")
        return {"keywords": keywords}
    except Exception as e:
        logger.error(f"Keyword extraction error: {e}")
        return {"keywords": [state.get("query", "")]}
