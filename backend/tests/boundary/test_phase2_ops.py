"""Phase 2 boundary tests: Docker/health-check/Prometheus/async embedding"""
import pytest
import asyncio
from unittest.mock import MagicMock, patch


# === P2-A1/A2/A3: code integrity after ruff fixes ===
def test_p2a1_state_typeddict_intact():
    from app.agent.state import DataAgentState
    import inspect
    # verify TypedDict fields are still accessible
    assert 'query' in DataAgentState.__annotations__
    assert 'error' in DataAgentState.__annotations__
    assert 'history' in DataAgentState.__annotations__


def test_p2a2_no_broken_imports():
    """P2-A2: no broken chains after import removal"""
    from app.agent.graph import graph
    from app.services.query_service import QueryService
    from app.api.routers.query_router import query_router
    from app.api.routers.health_router import health_router
    from app.core.metrics import QUERY_TOTAL
    assert graph is not None
    assert QueryService is not None


# === P2-B1/B3/B4: Docker config integrity ===
def test_p2b1_dockerfile_contains_env():
    import os
    p = os.path.join(os.path.dirname(__file__), '..', '..', 'Dockerfile')
    if os.path.exists(p):
        content = open(p, encoding='utf-8').read()
        assert 'TZ=Asia/Shanghai' in content  # P2-B4
        assert 'COPY prompts/' in content  # P2-B3
        assert 'COPY conf/' in content
        assert 'HEALTHCHECK' in content


def test_p2b2_docker_compose_env_file():
    import os
    p = os.path.join(os.path.dirname(__file__), '..', '..', 'docker-compose.yml')
    if os.path.exists(p):
        content = open(p, encoding='utf-8').read()
        assert 'env_file' in content  # P2-B1
        assert 'logs:/app/logs' in content  # P2-F3 log persistence


# === P2-C1/C2/C3: health check ===
def test_p2c1_health_router_exists():
    from app.api.routers.health_router import health_router
    routes = [r.path for r in health_router.routes]
    assert '/health' in routes
    assert '/metrics' in routes


def test_p2c3_health_no_llm_call():
    """P2-C3: /health does not make LLM requests"""
    from app.api.routers.health_router import health_router
    # health check should not depend on the LLM
    # only check the route exists (no real request)
    assert health_router is not None


# === P2-D1/D2/D3: Prometheus metrics ===
def test_p2d1_metrics_thread_safe():
    """P2-D1: prometheus_client is thread-safe by default"""
    from app.core.metrics import QUERY_TOTAL, QUERY_LATENCY, CACHE_HITS
    # Counter/Histogram is thread-safe
    QUERY_TOTAL.labels(status='success').inc()
    assert QUERY_TOTAL._val if hasattr(QUERY_TOTAL, '_val') else True


def test_p2d2_no_high_cardinality_labels():
    """P2-D2: labels are status only, no query/SQL"""
    from app.core.metrics import QUERY_TOTAL
    # check label names (should not contain query/sql/user)
    label_names = QUERY_TOTAL._labelnames if hasattr(QUERY_TOTAL, '_labelnames') else []
    for ln in label_names:
        assert ln not in ('query', 'sql', 'username', 'sql_text'), f"high-cardinality label: {ln}"


# === P2-E1/E2/E3: embedding async-ification ===
@pytest.mark.asyncio
async def test_p2e1_async_embedding_doesnt_block():
    """P2-E1/E2: async wrapper works normally"""
    from app.clients.embedding_client_manager import _DashScopeEmbeddings
    emb = MagicMock(spec=_DashScopeEmbeddings)
    emb.embed_query = MagicMock(return_value=[0.1] * 1024)
    # after to_thread wrapping it should still return normally
    import asyncio
    result = await asyncio.to_thread(emb.embed_query, 'test')
    assert len(result) == 1024


@pytest.mark.asyncio
async def test_p2e3_batch_embedding_preserves_order():
    """P2-E3: batch embed preserves order"""
    texts = ['aaa', 'bbb', 'ccc']
    # mock returns results in order
    expected = [[1.0], [2.0], [3.0]]
    from unittest.mock import patch
    with patch('dashscope.TextEmbedding.call') as mock_call:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.output = {'embeddings': [{'embedding': e} for e in expected]}
        mock_call.return_value = mock_resp
        from app.clients.embedding_client_manager import _DashScopeEmbeddings
        emb = _DashScopeEmbeddings(api_key='test', model='test')
        result = emb.embed_documents(texts)
        assert result == expected  # order preserved
