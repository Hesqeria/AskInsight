from datetime import datetime
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, DateInfoState
from app.core.log import logger


async def add_extra_context(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Supplement Context"})
    try:
        dw_repo = runtime.context["dw_doris_repository"]
        today = datetime.today()
        date_info = DateInfoState(
            date=today.strftime("%Y-%m-%d"),
            current_date_id=int(today.strftime("%Y%m%d")),
            current_month=int(today.strftime("%Y%m")),
            current_year=today.year,
            weekday=today.strftime("%A"),
            quarter=f"Q{(today.month - 1) // 3 + 1}",
        )
        db_info = await dw_repo.get_db_info()
        return {"date_info": date_info, "db_info": db_info}
    except Exception as e:
        logger.error(f"Supplement context error: {e}")
        raise
