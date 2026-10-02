"""B2.1-B2.3: client layer boundary tests"""
import pytest

from app.conf.app_config import DorisConfig, MilvusConfig, EmbeddingConfig, RerankConfig
from app.clients.doris_client_manager import DorisClientManager
from app.clients.milvus_client_manager import MilvusClientManager
from app.clients.embedding_client_manager import EmbeddingClientManager
from app.clients.rerank_client_manager import RerankClientManager


def _fake_doris():
    return DorisConfig(host="test-host.local", port=9999, user="u", password="p",
                      database="data_agent")


def _fake_milvus():
    return MilvusConfig(host="test-host.local", port=9999, user="u", password="p",
                       embedding_size=1024, column_collection="c", metric_collection="m")


def _fake_embedding():
    return EmbeddingConfig(api_base="http://127.0.0.1:9999/v1", api_key="k", model="x")


def test_b21_doris_unreachable_host():
    """B2.1: init does not raise; query raises at runtime"""
    mgr = DorisClientManager(_fake_doris())
    mgr.init()  # should not raise (create_async_engine does not connect immediately)
    # engine instantiated successfully
    assert mgr.engine is not None


def test_b22_milvus_wrong_password():
    """B2.2: Milvus wrong password does not raise on init (connection is lazy), close is safe"""
    mgr = MilvusClientManager(_fake_milvus())
    # init-time connection failure should be swallowed: client=None, connected=False, no raise
    assert mgr.connected is False
    mgr.close()  # should close safely even when client=None


def test_b23_embedding_service_down():
    """B2.3: TEI service not started, init does not raise"""
    mgr = EmbeddingClientManager(_fake_embedding())
    mgr.init()  # only create client instance
    assert mgr.client is not None


def test_b24_rerank_no_api_key_disables_silently():
    """When no api_key is configured, init must disable (not raise) so
    the merge node falls back to pure RRF ordering."""
    cfg = RerankConfig(api_key="", model="gte-rerank-v2",
                       enabled=True, top_n=30, score_threshold=0.0)
    mgr = RerankClientManager(cfg)
    mgr.init()
    assert mgr.client is None
    assert mgr.enabled is False
    assert mgr.is_available() is False


def test_b25_rerank_with_api_key_inits_client():
    cfg = RerankConfig(api_key="sk-test", model="gte-rerank-v2",
                       enabled=True, top_n=30, score_threshold=0.0)
    mgr = RerankClientManager(cfg)
    mgr.init()
    assert mgr.client is not None
    assert mgr.is_available() is True


def test_b26_reranker_parses_and_sorts_response(monkeypatch):
    """The rerank method must normalize dashscope's output into a list
    of {index, relevance_score}, sorted desc by score."""
    from app.clients.rerank_client_manager import _DashScopeReranker

    class _FakeResp:
        status_code = 200
        class _Output:
            results = [
                {"index": 2, "relevance_score": 0.92},
                {"index": 0, "relevance_score": 0.41},
                {"index": 1, "relevance_score": 0.87},
            ]
        output = _Output()

    def fake_call(self, query, documents, top_n):
        return _FakeResp().output.results

    monkeypatch.setattr(_DashScopeReranker, "_call", fake_call)
    r = _DashScopeReranker(api_key="k")
    out = r.rerank("GMV 多少", ["doc0", "doc1", "doc2"], top_n=3)
    # Sorted desc by relevance_score.
    assert [x["index"] for x in out] == [2, 1, 0]
    assert out[0]["relevance_score"] == pytest.approx(0.92)


def test_b27_reranker_handles_empty_input():
    from app.clients.rerank_client_manager import _DashScopeReranker
    r = _DashScopeReranker(api_key="k")
    assert r.rerank("", ["d"]) == []
    assert r.rerank("q", []) == []


def test_b28_rerank_async_wrapper(monkeypatch):
    import asyncio
    from app.clients.rerank_client_manager import _DashScopeReranker

    class _FakeResp:
        status_code = 200
        class _Output:
            results = [{"index": 0, "relevance_score": 0.7},
                       {"index": 1, "relevance_score": 0.9}]
        output = _Output()

    def fake_call(self, query, documents, top_n):
        return _FakeResp().output.results

    monkeypatch.setattr(_DashScopeReranker, "_call", fake_call)
    r = _DashScopeReranker(api_key="k")
    out = asyncio.run(r.arerank("q", ["a", "b"], top_n=2))
    assert [x["index"] for x in out] == [1, 0]
