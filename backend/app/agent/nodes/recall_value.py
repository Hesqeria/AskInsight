from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.keywords import sanitize_keywords
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt
from app.agent.llm import fast_llm as llm


async def recall_value(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Recall Column Values"})
    value_repository = runtime.context["value_doris_repository"]
    try:
        keywords = state.get("keywords", [])
        query = state.get("query", "")
        try:
            # Latency gate (parity with recall_column): jieba keywords
            # usually suffice - only expand when they are scarce.
            import os as _os
            _min_kw = int(_os.getenv("AUX_EXPAND_MIN_KEYWORDS", "3"))
            if len(set(keywords)) >= _min_kw:
                result = []
            else:
                prompt = PromptTemplate(
                    template=load_prompt("extend_keywords_for_value_recall"),
                    input_variables=["query"],
                )
                chain = prompt | llm | JsonOutputParser()
                result = await chain.ainvoke({"query": query})
            keywords = list(set(keywords + list(result)))
        except Exception as e:
            logger.warning(f"LLM expansion failed: {e}")

        values_map = {}
        keywords = sanitize_keywords(keywords)
        # Parallel LIKE lookups (serial x20 cost ~60s on the demo DW;
        # gathered in batches this drops to a single round-trip band).
        kws = keywords[:20]
        semaphore = asyncio.Semaphore(6)

        async def _lookup(kw):
            async with semaphore:
                try:
                    return await value_repository.search_safe(kw)
                except Exception:
                    return []

        batches = await asyncio.gather(*(_lookup(kw) for kw in kws))
        for values in batches:
            for v in values:
                vid = v.get("id")
                if vid and vid not in values_map:
                    values_map[vid] = v
        return {"retrieved_values": list(values_map.values())}
    except Exception as e:
        logger.error(f"Recall column values error: {e}")
        return {"retrieved_values": []}  # B4.3 node-level fallback
