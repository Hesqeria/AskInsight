import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.agent.nodes.generate_sql import _clean_sql
from app.core.log import logger
from app.core.metrics import SQL_CORRECTED
from app.prompt.prompt_loader import load_prompt


async def correct_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Correct SQL"})
    sql = state.get("sql", "")
    if not sql or not sql.strip():
        logger.info("Empty SQL, skipping correction")
        return {"sql": sql, "error": None}
    try:
        prompt = PromptTemplate(
            template=load_prompt("correct_sql"),
            input_variables=["query", "table_infos", "metric_infos",
                             "date_info", "db_info", "error", "sql"],
        )
        chain = prompt | llm | StrOutputParser()
        sql = await chain.ainvoke({
            "query": state["query"],
            "table_infos": yaml.dump(state.get("table_infos", []), allow_unicode=True, sort_keys=False),
            "metric_infos": yaml.dump(state.get("metric_infos", []), allow_unicode=True, sort_keys=False),
            "date_info": yaml.dump(state.get("date_info", {}), allow_unicode=True, sort_keys=False),
            "db_info": yaml.dump(state.get("db_info", {}), allow_unicode=True, sort_keys=False),
            "error": state.get("error", ""),
            "sql": state.get("sql", ""),
        })
        logger.info("SQL correction done")
        SQL_CORRECTED.inc()
        return {"sql": _clean_sql(sql), "error": None}
    except Exception as e:
        logger.error(f"SQL correction error: {e}")
        raise
