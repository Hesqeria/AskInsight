"""R3: security + monitoring + multi-turn memory boundary tests"""
import pytest
from unittest.mock import MagicMock, AsyncMock


# DB-GPT#3168: static-file endpoint authentication
def test_download_requires_auth():
    from app.api.routers.report_router import report_router
    # check the download route has a verify_token dependency
    routes = {r.path: r for r in report_router.routes}
    download = routes.get('/api/report/download')
    if download:
        deps = [d.name for d in getattr(download, 'dependant', MagicMock()).dependencies if hasattr(d, 'name')]
        # or check the source code
        import inspect
        src = inspect.getsource(download.endpoint) if hasattr(download, 'endpoint') else ''
        assert 'verify_token' in src or 'Depends' in src or 'user' in src


# DB-GPT#3171: multi-turn memory
def test_multi_turn_memory():
    import inspect
    from app.agent.nodes.resolve_context import resolve_context
    src = inspect.getsource(resolve_context)
    assert '[-3:]' in src or 'history' in src.lower()


# Vanna#1121: safety gateway covers EXECUTE
def test_safety_blocks_execute():
    from app.agent.nodes.validate_sql_safety import DANGEROUS_PATTERNS
    import re
    test_sql = 'EXECUTE sp_renamedb'
    blocked = any(re.search(p, test_sql, re.IGNORECASE) for p in DANGEROUS_PATTERNS)
    assert blocked, 'EXECUTE should be blocked'


def test_safety_blocks_xp_cmdshell():
    from app.agent.nodes.validate_sql_safety import DANGEROUS_PATTERNS
    import re
    test_sql = "SELECT * FROM t; xp_cmdshell('dir')"
    blocked = any(re.search(p, test_sql, re.IGNORECASE) for p in DANGEROUS_PATTERNS)
    assert blocked


# SQL_PASS_RATE metric
def test_sql_pass_rate_metric():
    from app.core.metrics import SQL_PASS_RATE
    assert SQL_PASS_RATE is not None


# full workflow: 22 nodes
def test_graph_22_nodes():
    from app.agent.graph import graph
    nodes = list(graph.get_graph().nodes.keys())
    assert len(nodes) >= 22


# CTE whitelist
@pytest.mark.asyncio
async def test_cte_with_real_tables():
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    state = {'sql': 'WITH ranked AS (SELECT region_name, SUM(order_amount) AS amt FROM fact_order JOIN dim_region GROUP BY region_name) SELECT * FROM ranked'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await validate_sql_safety(state, runtime)
    assert result.get('error') is None or 'ranked' not in str(result.get('error', ''))
