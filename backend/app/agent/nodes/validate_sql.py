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
        from app.agent.events import emit
        emit("sql/validated", {"ok": True})
        # SQLBot #1202 practice: LIMIT inside a subquery truncates the
        # rows feeding an outer aggregation - reject so correct_sql fixes
        # the placement instead of silently returning wrong numbers.
        inner = _find_inner_limit(sql)
        if inner:
            raise ValueError(
                f"LIMIT found inside subquery at depth {inner} - move it "
                "to the outermost query")
        return {"error": None}
    except Exception as e:
        logger.error(f"SQL validation failed: {e}")
        SQL_PASS_RATE.set(0)
        from app.agent.events import emit
        emit("sql/validated", {"ok": False, "error": str(e)[:300]})
        return {"error": str(e)}


def _find_inner_limit(sql: str) -> int:
    """Return paren depth of the first LIMIT that is NOT at top level.

    0 means all LIMITs are top-level (ok); >0 is an inner LIMIT (bad).
    """
    depth = 0
    i, n = 0, len(sql)
    in_str = None
    while i < n:
        c = sql[i]
        if in_str:
            if c == in_str:
                in_str = None
        elif c in ("'", '"', '`'):
            in_str = c
        elif c == '(':
            depth += 1
        elif c == ')':
            depth = max(0, depth - 1)
        elif depth > 0 and sql[i:i + 5].upper() == 'LIMIT':
            after = sql[i + 5: i + 6]
            if after in ('', ' ', chr(10), chr(9), ';'):
                return depth
        i += 1
    return 0
