from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger

MAX_RESULT_ROWS = 1000


async def execute_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Execute SQL"})
    try:
        sql = state["sql"]
        repo = runtime.context["dw_doris_repository"]
        result = await repo.execute_sql(sql)
        total = len(result)
        truncated = total > MAX_RESULT_ROWS
        if truncated:
            result = result[:MAX_RESULT_ROWS]
            logger.warning(f"SQL result truncated: {total} -> {MAX_RESULT_ROWS} rows")
        logger.info(f"SQL execution result: {total} rows (returned {len(result)})")
        if not result:
            writer({"result": [{"hint": "Query result is empty. Possible reasons: 1) data does not cover this condition 2) no match in the date range"}]})
        else:
            writer({"result": result, "truncated": truncated, "total_rows": total})
        return {"_last_result": result, "_result_truncated": truncated, "_total_rows": total}
    except Exception as e:
        logger.error(f"SQL execution error: {e}")
        raise
