"""Guard: vector recall must actually run (embed_cache import is inside
the node body - a missing module used to degrade silently to no recall)."""
import sys
import asyncio

import pytest
from unittest.mock import patch

from app.agent.nodes.recall_column import recall_column as recall_column_node
from app.agent.nodes.recall_metric import recall_metric as recall_metric_node


class _FakeEmbed:
    async def aembed_query(self, t):
        return [0.1, 0.2]

    async def aembed_documents(self, ts):
        return [[0.1, 0.2] for _ in ts]


class _FakeRepo:
    def __init__(self):
        self.searched = []

    async def async_search_safe(self, embedding, limit=10):
        self.searched.append(round(embedding[0], 3))
        return [{"id": "dw.ads_gmv_total_day.gmv", "name": "gmv", "score": 0.9,
                 "table": "dw.ads_gmv_total_day", "columns": [{"name": "gmv"}]}]


class _FakeValueRepo:
    async def async_search_safe(self, *a, **k):
        return []


class _RT:
    def __init__(self, ctx):
        self.context = ctx

    @property
    def stream_writer(self):
        return lambda p: None


@pytest.fixture(autouse=True)
def _no_llm_expand(monkeypatch):
    """Hermetic: skip the fast-LLM keyword expansion branch (network)."""
    monkeypatch.setenv("AUX_EXPAND_MIN_KEYWORDS", "0")


@pytest.fixture(autouse=True)
def _isolated_embed_cache(monkeypatch):
    """recall_column batch-warms the GLOBAL embed cache; keep this test's
    fake vectors from leaking into other tests."""
    from app.core import embed_cache
    saved = dict(embed_cache._cache)
    monkeypatch.setattr(embed_cache, "_cache", {})
    yield
    embed_cache._cache.clear()
    embed_cache._cache.update(saved)


def test_recall_column_uses_vector_search():
    repo = _FakeRepo()
    ctx = {"embedding_client": _FakeEmbed(),
           "column_milvus_repository": repo,
           "metric_milvus_repository": _FakeRepo(),
           "value_doris_repository": _FakeValueRepo()}
    state = {"query": "昨天GMV是多少", "keywords": ["GMV", "昨天"]}
    upd = asyncio.run(recall_column_node(dict(state), _RT(ctx)))
    assert repo.searched, "vector search never ran - embed path broken"
    assert upd.get("retrieved_columns")


def test_recall_metric_uses_vector_search():
    repo = _FakeRepo()
    ctx = {"embedding_client": _FakeEmbed(),
           "column_milvus_repository": _FakeRepo(),
           "metric_milvus_repository": repo,
           "value_doris_repository": _FakeValueRepo()}
    state = {"query": "上月订单数", "keywords": ["订单数", "上月"]}
    upd = asyncio.run(recall_metric_node(dict(state), _RT(ctx)))
    assert repo.searched, "metric vector search never ran"
