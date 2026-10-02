"""SQL lineage extraction node: parse generated SQL -> column-level lineage -> store in Doris sql_lineage"""
import re
import uuid
from datetime import datetime
from langgraph.runtime import Runtime
from sqlalchemy import text

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


def extract_lineage_from_sql(sql: str) -> list:
    """Extract column-level lineage relationships from SQL

    Returns:
        [{"target_column": "...", "source_table": "...", "source_column": "...", "transformation": "SUM"}]

    PRD P3: sqlglot AST first; legacy regex kept as fallback when parsing fails.
    """
    if not sql or not sql.strip():
        return []
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):
        return []

    from app.core.sql_lineage_engine import extract_lineage_sqlglot
    result = extract_lineage_sqlglot(sql)
    if result is None:
        # PRD P3: unparseable SQL yields no lineage records (manual review
        # semantics) rather than regex guesses.
        logger.info("sqlglot parse failed; no lineage extracted")
        return []
    return result


async def extract_lineage(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Extract lineage after execute_sql"""
    writer = runtime.stream_writer
    writer({"stage": "Lineage Extraction"})
    try:
        sql = state.get("sql", "")
        if not sql:
            return {}

        lineage_list = extract_lineage_from_sql(sql)
        if not lineage_list:
            logger.info("SQL has no extractable lineage (may be a message/simple query)")
            return {}

        # Store into Doris sql_lineage
        meta_repo = runtime.context["meta_doris_repository"]
        request_id = state.get("_request_id", "")

        for lin in lineage_list:
            lin_id = str(uuid.uuid4())
            await meta_repo.session.execute(text("""
                INSERT INTO data_agent.sql_lineage (id, request_id, sql_text, target_table, target_column,
                    source_table, source_column, transformation, created_at)
                VALUES (:id, :rid, :sql, :tt, :tc, :st, :sc, :tf, :ca)
            """), {
                "id": lin_id, "rid": request_id,
                "sql": sql[:2000],
                "tt": "", "tc": lin["target_column"],
                "st": lin["source_table"], "sc": lin["source_column"],
                "tf": lin["transformation"],
                "ca": datetime.now(),
            })
        await meta_repo.session.commit()

        logger.info(f"Lineage extraction: {len(lineage_list)} column-level lineage records")
        writer({"lineage": lineage_list[:5]})  # SSE push first 5 records
        return {}
    except Exception as e:
        logger.error(f"Lineage extraction error: {e}")
        return {}
