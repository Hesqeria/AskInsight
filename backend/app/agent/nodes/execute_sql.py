from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def execute_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Execute SQL"})
    try:
        sql = state["sql"]
        repo = runtime.context["dw_doris_repository"]
        result = await repo.execute_sql(sql)
        logger.info(f"SQL execution result: {len(result)} rows")
        # SQLBot#508: friendly hint for empty results
        if not result:
            writer({"result": [{"hint": "Query result is empty. Possible reasons: 1) data does not cover this condition 2) no match in the date range"}]})
        else:
            writer({"result": result})
        return {"_last_result": result}
    except Exception as e:
        logger.error(f"SQL execution error: {e}")
        raise
