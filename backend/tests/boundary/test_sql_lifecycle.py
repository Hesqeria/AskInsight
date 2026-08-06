"""B5.1-B5.2: SQL lifecycle tests"""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_b51_validate_sql_invalid_syntax_routes_to_correct():
    """B5.1: validate_sql raises on invalid SQL; the graph should route to correct_sql"""
    # directly verify the validate_sql node returns {"error": ...}
    from app.agent.nodes.validate_sql import validate_sql
    state = {"sql": "SELECT * FROM"}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    runtime.context = {
        "dw_mysql_repository": MagicMock(validate_sql=AsyncMock(side_effect=Exception("syntax err")))
    }
    result = await validate_sql(state, runtime)
    assert result["error"] is not None


@pytest.mark.asyncio
async def test_b52_correct_sql_still_fails_returns_error():
    """B5.2: when execute_sql fails after correct_sql, it should be converted to error by query_service"""
    # verify the execute_sql node's raising behavior (query_service should try/except)
    from app.agent.nodes.execute_sql import execute_sql
    state = {"sql": "INVALID SQL"}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    runtime.context = {
        "dw_mysql_repository": MagicMock(execute_sql=AsyncMock(side_effect=Exception("exec failed")))
    }
    with pytest.raises(Exception):
        await execute_sql(state, runtime)
    # note: the node raising is expected; the query_service layer is responsible for converting to SSE error
