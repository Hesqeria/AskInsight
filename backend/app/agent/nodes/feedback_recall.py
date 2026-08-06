"""Get-better-with-each-question node: recall historical examples of corrected SQL"""
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from sqlalchemy import text


async def feedback_recall(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Historical Experience Recall"})
    try:
        meta_repo = runtime.context["meta_doris_repository"]
        query = state.get("query", "")

        # Query feedback_log for corrected SQL of similar queries
        sql = """
            SELECT query, corrected_sql FROM feedback_log
            WHERE query LIKE :pattern
            ORDER BY created_at DESC LIMIT 3
        """
        result = await meta_repo.session.execute(text(sql), {"pattern": f"%{query[:10]}%"})
        examples = []
        for row in result.fetchall():
            sql_text = row[1] or ''
            # P3-B4: filter out SQL containing dangerous operations
            import re as _re
            if _re.search(r'(drop|delete|truncate|alter|insert|update)', sql_text, _re.IGNORECASE):
                continue
            examples.append({"query": row[0], "sql": sql_text})

        logger.info(f"Historical experience recall: {len(examples)} items")
        return {"feedback_examples": examples}
    except Exception as e:
        logger.error(f"Historical experience recall error: {e}")
        return {"feedback_examples": []}
