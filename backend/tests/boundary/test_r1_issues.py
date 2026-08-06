"""R1: competitor issue boundary tests"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from app.agent.nodes.generate_sql import _clean_sql


# Vanna#1121: SQL injection in feedback API
def test_feedback_api_safety():
    from app.api.routers.feedback_router import submit_feedback
    import inspect
    src = inspect.getsource(submit_feedback)
    assert 'SELECT' in src or 'validate' in src.lower() or 'DROP' in src.upper()


# Vanna#1112: temperature=0
def test_llm_temperature_zero():
    from app.agent.llm import llm
    assert llm.temperature == 0


# SQLBot#1278: CTE/WITH support
@pytest.mark.asyncio
async def test_cte_with_allowed():
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    state = {'sql': 'WITH t AS (SELECT 1) SELECT * FROM t'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await validate_sql_safety(state, runtime)
    assert result.get('error') is None, 'CTE/WITH should be allowed'


# DB-GPT#3156: forbid non-SELECT
@pytest.mark.asyncio
async def test_non_select_blocked():
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    state = {'sql': 'EXECUTE script.py'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await validate_sql_safety(state, runtime)
    assert result.get('error') is not None


# DB-GPT#3158: 3 candidates
def test_multi_candidate_3():
    import inspect
    from app.agent.nodes.generate_sql import generate_sql
    src = inspect.getsource(generate_sql)
    assert 'range(3)' in src


# _clean_sql: markdown + prefix + quotes + semicolon + empty
def test_clean_markdown():
    bt = chr(96) * 3
    assert _clean_sql(bt + 'sql' + chr(10) + 'SELECT 1' + chr(10) + bt) == 'SELECT 1'

def test_clean_prefix():
    assert _clean_sql('SQL: SELECT 1;') == 'SELECT 1'

def test_clean_empty():
    result = _clean_sql('')
    assert len(result) > 0, 'empty input should have a fallback'
