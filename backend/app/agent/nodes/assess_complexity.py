"""SQL complexity assessment node: tiered routing

Three-tier classification:
  simple  -> fully automatic execution
  medium  -> execute but attach risk warning
  complex -> degrade to "recommend manual review" and provide SQL framework
"""
import re
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


def _analyze_sql_complexity(sql: str):
    """Analyze SQL complexity, return (level, details)"""
    sql_upper = sql.upper()
    if "not exist in system" in sql.lower() or "column does not exist" in sql.lower():
        return "fallback", {"reason": "LLM returned fallback message"}

    details = {}
    tables_in_from = len(re.findall(r'\bFROM\b', sql_upper))
    tables_in_join = len(re.findall(r'\bJOIN\b', sql_upper))
    details["tables"] = tables_in_from + tables_in_join

    subquery_count = len(re.findall(r'\(\s*SELECT\b', sql_upper))
    details["subqueries"] = subquery_count

    window_count = len(re.findall(
        r'\b(RANK|DENSE_RANK|ROW_NUMBER|LAG|LEAD|NTILE|SUM|AVG|COUNT|MAX|MIN)\s*\([^)]*\)\s*OVER\s*\(',
        sql_upper))
    details["windows"] = window_count
    details["unions"] = len(re.findall(r'\bUNION\b', sql_upper))

    group_match = re.search(r'\bGROUP\s+BY\b\s*(.+?)(?:\bHAVING\b|\bORDER\b|\bLIMIT\b|$)', sql_upper, re.DOTALL)
    details["group_by_cols"] = len(re.findall(r',', group_match.group(1))) + 1 if group_match else 0
    details["has_having"] = 1 if re.search(r'\bHAVING\b', sql_upper) else 0
    details["case_when"] = len(re.findall(r'\bCASE\b', sql_upper))
    details["has_distinct"] = 1 if re.search(r'\bDISTINCT\b', sql_upper) else 0

    score = 0
    score += details["tables"] * 10
    score += details["subqueries"] * 20
    score += details["windows"] * 25
    score += details["unions"] * 30
    score += details["group_by_cols"] * 3
    score += details["has_having"] * 10
    score += details["case_when"] * 15
    score += details["has_distinct"] * 5

    details["score"] = score
    if score <= 15:
        level = "simple"
    elif score <= 40:
        level = "medium"
    else:
        level = "complex"
    return level, details


async def assess_complexity(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """SQL complexity assessment node"""
    writer = runtime.stream_writer
    writer({"stage": "Complexity Assessment"})
    try:
        sql = state.get("sql", "")
        if not sql:
            return {"complexity": "unknown", "complexity_detail": {}}
        level, details = _analyze_sql_complexity(sql)
        logger.info(
            f"SQL complexity: {level} "
            f"(score={details.get('score',0)}, tables={details.get('tables',0)}, "
            f"subs={details.get('subqueries',0)}, windows={details.get('windows',0)})"
        )
        if level == "complex":
            logger.warning(f"SQL too complex, recommend manual review: {sql[:200]}...")
        return {"complexity": level, "complexity_detail": details}
    except Exception as e:
        logger.error(f"Complexity assessment error: {e}")
        return {"complexity": "unknown", "complexity_detail": {}}
