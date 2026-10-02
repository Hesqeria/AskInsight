import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import fast_llm as llm
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def filter_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Filter Metrics"})
    try:
        query = state["query"]
        metric_infos = state.get("metric_infos", [])
        if not metric_infos:
            return {"metric_infos": []}

        prompt = PromptTemplate(
            template=load_prompt("filter_metric_info"),
            input_variables=["query", "metric_infos"],
        )
        chain = prompt | llm | JsonOutputParser()

        try:
            result = await chain.ainvoke({
                "query": query,
                "metric_infos": yaml.dump(metric_infos, allow_unicode=True, sort_keys=False),
            })
        except Exception as e:
            logger.warning(f"filter_metric JSON parsing failed: {e}")
            return {"metric_infos": metric_infos}

        sel = set(result if isinstance(result, list) else [])
        if sel:
            metric_infos = [m for m in metric_infos if m.get("name") in sel]
        logger.info("Filter metrics done")
        return {"metric_infos": metric_infos}
    except Exception as e:
        logger.error(f"Filter metrics error: {e}")
        return {"metric_infos": state.get("metric_infos", [])}
