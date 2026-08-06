"""Issue optimization boundary tests: MCP + coloring + language detection + retry + connection pool"""
import pytest
import asyncio


# === MCP Server ===
def test_mcp_server_exists():
    from mcp_server.server import DataAgentMCP
    agent = DataAgentMCP('http://localhost:8000')
    assert agent.endpoint == 'http://localhost:8000'

def test_mcp_list_tables():
    from mcp_server.server import DataAgentMCP
    agent = DataAgentMCP('http://localhost:8000')
    result = agent.list_tables()
    assert 'fact_order' in result
    assert 'dim_region' in result

def test_mcp_get_schema():
    from mcp_server.server import DataAgentMCP
    agent = DataAgentMCP('http://localhost:8000')
    schema = agent.get_schema('fact_order')
    assert 'order_amount' in schema
    assert 'UNKNOWN_TABLE' not in schema


# === Result coloring (SQLBot#1213) ===
def test_color_positive():
    from app.services.result_formatter import format_result_with_color
    data = [{'region': 'A', 'amt': 150}]
    result = format_result_with_color(data, baseline_avg=100)
    assert result[0].get('_amt_color') == '#2ecc71'

def test_color_negative():
    from app.services.result_formatter import format_result_with_color
    data = [{'region': 'A', 'amt': 30}]
    result = format_result_with_color(data, baseline_avg=100)
    assert result[0].get('_amt_color') == '#e74c3c'

def test_color_no_baseline():
    from app.services.result_formatter import format_result_with_color
    data = [{'region': 'A', 'amt': 100}]
    result = format_result_with_color(data)
    assert '_amt_color' not in result[0]  # no baseline, no coloring


# === Language detection (SQLBot#1119/DB-GPT#3152) ===
def test_lang_chinese():
    from app.services.result_formatter import detect_language
    assert detect_language('\u5404\u5730\u533a\u9500\u552e\u603b\u989d') == 'zh'

def test_lang_english():
    from app.services.result_formatter import detect_language
    assert detect_language('show me total sales by region') == 'en'

def test_lang_empty():
    from app.services.result_formatter import detect_language
    assert detect_language('') == 'zh'


# === LLM retry (DB-GPT#669) ===
@pytest.mark.asyncio
async def test_llm_retry_timeout():
    from app.core.llm_retry import safe_ainvoke
    from unittest.mock import AsyncMock, MagicMock
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError())
    with pytest.raises(RuntimeError):
        await safe_ainvoke(mock_llm, [], retries=1, timeout=1)

@pytest.mark.asyncio
async def test_llm_retry_success_second():
    from app.core.llm_retry import safe_ainvoke
    from unittest.mock import AsyncMock, MagicMock
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=[Exception('fail'), MagicMock(content='OK')])
    result = await safe_ainvoke(mock_llm, [], retries=2, timeout=10)
    assert result is not None


# === Connection pool health check ===
def test_health_has_pool():
    from pathlib import Path
    src = Path('app/api/routers/health_router.py').read_text(encoding='utf-8')
    assert 'doris_pool' in src


# === Admin API completeness ===
def test_admin_llm_switch():
    from app.api.routers.admin_router import admin_router
    paths = [r.path for r in admin_router.routes]
    assert '/api/admin/llm/switch' in paths
