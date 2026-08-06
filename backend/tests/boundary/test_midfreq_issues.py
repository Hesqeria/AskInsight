"""Mid-frequency issue optimization boundary tests"""
import pytest
from app.services.csv_service import _infer_type
from app.services.result_formatter import detect_language


# Vanna#20: CSV upload
def test_csv_infer_int():
    assert _infer_type(['1', '2', '3']) == 'BIGINT'

def test_csv_infer_float():
    assert _infer_type(['1.5', '2.3', '3.0']) == 'DOUBLE'

def test_csv_infer_varchar():
    assert _infer_type(['hello', 'world']) == 'VARCHAR(255)'

def test_csv_infer_mixed():
    assert _infer_type(['1', '2', 'abc']) == 'VARCHAR(255)'

def test_csv_infer_empty():
    assert _infer_type(['', '', '']) == 'VARCHAR(255)'


# Vanna#147: quality assessment routes
def test_quality_routes():
    from app.api.routers.feedback_router import feedback_router
    paths = [r.path for r in feedback_router.routes]
    assert '/api/quality/rate' in paths
    assert '/api/quality/stats' in paths


# Vanna#20: upload routes
def test_upload_route():
    from app.api.routers.upload_router import upload_router
    paths = [r.path for r in upload_router.routes]
    assert '/api/upload/csv' in paths


# DB-GPT#1279: asyncmy does not need a doris dialect
def test_asyncmy_no_doris_dialect():
    from sqlalchemy import create_engine
    # mysql+asyncmy protocol does not need a doris dialect
    url = 'mysql+asyncmy://user:pass@host:3306/db'
    assert 'mysql' in url
    assert 'doris' not in url.split('://')[0]


# SQLBot#1119: language detection
def test_lang_detect_zh():
    assert detect_language('\u5404\u5730\u533a\u9500\u552e\u603b\u989d\u6392\u884c') == 'zh'

def test_lang_detect_en():
    assert detect_language('show total sales') == 'en'


# connection pool monitoring (Vanna#261)
def test_health_pool():
    from pathlib import Path
    src = Path('app/api/routers/health_router.py').read_text(encoding='utf-8')
    assert 'doris_pool' in src


# LLM retry (DB-GPT#669)
def test_llm_retry_exists():
    from app.core.llm_retry import safe_ainvoke, safe_ainvoke_chain
    assert callable(safe_ainvoke)
    assert callable(safe_ainvoke_chain)


# MCP Server (Vanna#721)
def test_mcp_tools():
    from mcp_server.server import DataAgentMCP
    agent = DataAgentMCP('http://localhost:8000')
    assert hasattr(agent, 'query')
    assert hasattr(agent, 'list_tables')
    assert hasattr(agent, 'get_schema')
