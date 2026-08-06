from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def recall_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Recall Metrics"})
    embedding_client = runtime.context["embedding_client"]
    metric_repository = runtime.context["metric_milvus_repository"]
    try:
        keywords = state.get("keywords", [])
        query = state.get("query", "")
        try:
            prompt = PromptTemplate(
                template=load_prompt("extend_keywords_for_metric_recall"),
                input_variables=["query"],
            )
            chain = prompt | llm | JsonOutputParser()
            result = await chain.ainvoke({"query": query})
            keywords = set(list(keywords) + list(result))
        except Exception as e:
            logger.warning(f"LLM expansion failed: {e}")
            keywords = set(keywords)

        retrieved_map = {}
        for kw in keywords:
            embedding = await embedding_client.aembed_query(kw)
            payloads = await metric_repository.async_search_safe(embedding)
            for p in payloads:
                mid = p.get("id")
                if mid and mid not in retrieved_map:
                    retrieved_map[mid] = p
        return {"retrieved_metrics": list(retrieved_map.values())}
    except Exception as e:
        logger.error(f"Recall metrics error: {e}")
        return {"retrieved_metrics": []}
