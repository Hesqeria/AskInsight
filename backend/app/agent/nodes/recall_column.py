from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt
from app.agent.llm import llm


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
            result = await chain.ainvoke({"query": query})
            keywords = set(list(keywords) + list(result))
        except Exception as e:
            logger.warning(f"LLM keyword expansion failed, using original keywords: {e}")
            keywords = set(keywords)

        retrieved_map = {}
        for kw in keywords:
            embedding = await embedding_client.aembed_query(kw)
            payloads = await column_repository.async_search_safe(embedding, limit=10)
            for p in payloads:
                cid = p.get("id")
                if cid and cid not in retrieved_map:
                    retrieved_map[cid] = p
        return {"retrieved_columns": list(retrieved_map.values())}
    except Exception as e:
        logger.error(f"Recall columns error: {e}")
        return {"retrieved_columns": []}  # B4.2 node-level fallback
