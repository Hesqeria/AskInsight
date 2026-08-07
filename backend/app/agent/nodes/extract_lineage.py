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
    if not sql or not sql.strip():
        return []
    """Extract column-level lineage relationships from SQL

    Returns:
        [{"target_column": "...", "source_table": "...", "source_column": "...", "transformation": "SUM"}]
    """
    lineage = []
    if not sql:
        return lineage

    # Skip non-SELECT (DDL/DML/message)
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):
        return lineage

    try:
        import sqlparse
        parsed = sqlparse.parse(sql)[0]

        # 1. Extract FROM / JOIN table names + aliases
        table_alias_map = {}  # {alias: table_name}
        from_pattern = re.compile(
            r'(?:FROM|JOIN)\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?',
            re.IGNORECASE
        )
        for m in from_pattern.finditer(sql):
            tname = m.group(1)
            alias = m.group(2) or tname
            table_alias_map[alias.lower()] = tname
            table_alias_map[tname.lower()] = tname

        # 2. Extract SELECT columns -> source_table.column
        # Match: SUM(f.order_amount) AS total_sales
        # Match: f.region_name AS region
        # Match: r.region_name
        select_pattern = re.compile(
            r'(\w+)\s*\(\s*(\w+)\.(\w+)\s*\)\s*(?:AS\s+)?(\S+)'  # SUM(f.col) AS alias
            r'|(\w+)\.(\w+)\s*(?:AS\s+)?(\S+)?',  # t.col AS alias
            re.IGNORECASE
        )
        for m in select_pattern.finditer(sql):
            if m.group(1):  # aggregate function
                func = m.group(1).upper()
                alias_or_table = m.group(2).lower()
                col = m.group(3)
                target = (m.group(4) or col).rstrip(",")
                source_table = table_alias_map.get(alias_or_table, alias_or_table)
                lineage.append({
                    "target_column": target,
                    "source_table": source_table,
                    "source_column": col,
                    "transformation": func,
                })
            elif m.group(5):  # plain column reference
                alias_or_table = m.group(5).lower()
                col = m.group(6)
                target = (m.group(7) or col).rstrip(",").rstrip(")")
                # L-A2: skip Chinese aliases after AS as source columns
                if target and not _is_alias(target):
                    source_table = table_alias_map.get(alias_or_table, alias_or_table)
                    lineage.append({
                        "target_column": target,
                        "source_table": source_table,
                        "source_column": col,
                        "transformation": "DIRECT",
                    })
    except Exception as e:
        logger.warning(f"SQL lineage parsing failed: {e}")

    return lineage


def _is_alias(name: str) -> bool:
    """L-A2: determine whether it is a Chinese alias (not a real column name)"""
    if not name:
        return True
    # Chinese characters -> is an alias
    if any('\u4e00' <= c <= '\u9fff' for c in name):
        return True
    # SQL keyword -> skip
    if name.upper() in ("FROM", "WHERE", "GROUP", "ORDER", "LIMIT", "JOIN", "AND", "OR", "ON", "AS", "BY", "DESC", "ASC"):
        return True
    return False


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
                INSERT INTO sql_lineage (id, request_id, sql_text, target_table, target_column,
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
