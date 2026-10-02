from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.keywords import sanitize_keywords
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt
from app.agent.llm import fast_llm as llm


async def recall_column(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Recall Columns"})
    embedding_client = runtime.context["embedding_client"]
    column_repository = runtime.context["column_milvus_repository"]
    try:
        keywords = state.get("keywords", [])
        query = state.get("query", "")
        # B4.2 fallback: when LLM keyword expansion fails, only use the original keywords
        try:
            prompt = PromptTemplate(
                template=load_prompt("extend_keywords_for_column_recall"),
                input_variables=["query"],
            )
            chain = prompt | llm | JsonOutputParser()
            # Latency gate: a single reasoning model makes each expansion
            # cost ~30s. Jieba keywords usually suffice - only expand via
            # LLM when base keywords are scarce.
            import os as _os
            _min_kw = int(_os.getenv("AUX_EXPAND_MIN_KEYWORDS", "3"))
            if len(set(keywords)) >= _min_kw:
                result = []
            else:
                result = await chain.ainvoke({"query": query})
            keywords = set(list(keywords) + list(result))
        except Exception as e:
            logger.warning(f"LLM keyword expansion failed, using original keywords: {e}")
            keywords = set(keywords)

        # Batch-warm all keyword embeddings in one gateway call per chunk
        # (serial/throttled per-keyword embeds cost 10-60s on cold queries).
        try:
            from app.core.embed_cache import warm_many
            warmed = await warm_many(embedding_client,
                                     list(keywords) + [query])
            if warmed:
                logger.info(f"embed batch-warmed {warmed} terms")
        except Exception as we:
            logger.debug(f"embed warm skipped: {we}")

        retrieved_map = {}
        # BT-19: Milvus graceful degradation to keyword matching
        milvus_available = True
        try:
            _ = column_repository  # probe
        except Exception as e:
            logger.warning(f"Milvus unavailable, falling back to keyword-only recall: {e}")
            milvus_available = False

        if not milvus_available:
            return {"retrieved_columns": [], "recall_mode": "keyword_fallback"}

        keywords = sanitize_keywords(keywords)
        # Parallel embed+search: serial per-keyword embedding costs ~0.9s
        # each (up to 20 kw = ~18s wall); gather brings it to ~1s.
        import asyncio as _aio

        from app.core.embed_cache import embed_cached

        async def _one(kw):
            embedding = await embed_cached(embedding_client, kw)
            if embedding is None:
                embedding = await embedding_client.aembed_query(kw)
            return await column_repository.async_search_safe(embedding, limit=10)

        results = await _aio.gather(
            *[_one(kw) for kw in list(keywords)[:20]], return_exceptions=True)
        for payloads in results:
            if isinstance(payloads, BaseException):
                continue
            for p in payloads:
                cid = p.get("id")
                if cid and cid not in retrieved_map:
                    retrieved_map[cid] = p
        return {"retrieved_columns": list(retrieved_map.values())}
    except Exception as e:
        logger.error(f"Recall columns error: {e}")
        return {"retrieved_columns": []}  # B4.2 node-level fallback
