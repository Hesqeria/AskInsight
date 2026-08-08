from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.metrics import SQL_PASS_RATE


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Validate SQL"})
    try:
        sql = state["sql"]
        repo = runtime.context["dw_doris_repository"]
        await repo.validate_sql(sql)
        logger.info(f"SQL validation passed: {sql}")
        SQL_PASS_RATE.set(1)
        return {"error": None}
    except Exception as e:
        logger.error(f"SQL validation failed: {e}")
        SQL_PASS_RATE.set(0)
        return {"error": str(e)}
