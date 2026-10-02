"""Dual-path recall: RRF fusion + sparse n-gram + ontology query expansion."""
import pytest
from app.core.recall_fusion import (rrf_fuse, char_ngram_similarity,
                                    expand_with_aliases)


def test_rrf_merges_lanes():
    dense = ["A", "B", "C"]
    sparse = ["C", "D", "A"]
    out = rrf_fuse([dense, sparse], k=60)
    assert out[0] in ("A", "C")          # appears high in both lanes
    assert set(out) == {"A", "B", "C", "D"}


def test_rrf_weights_prioritize_dense():
    dense = ["A", "B"]
    sparse = ["B", "A"]
    out = rrf_fuse([dense, sparse], k=60, weights=[1.0, 0.1])
    assert out[0] == "A"


def test_char_ngram_sparse_similarity_chinese():
    s1 = char_ngram_similarity("消费金额最高的前3个客户", "消费金额前三的客户")
    s2 = char_ngram_similarity("消费金额最高的前3个客户", "各服务区GMV")
    assert s1 > 0.35 and s2 < 0.1


def test_expand_with_aliases_ontology():
    aliases = {"营收": "GMV", "加购件数": "cart_item_count"}
    q = expand_with_aliases("本月营收是多少", aliases)
    assert "GMV" in q
    q2 = expand_with_aliases("各服务区GMV", aliases)
    assert q2 == "各服务区GMV"


@pytest.mark.asyncio
async def test_exemplar_search_dual_path(monkeypatch):
    """Sparse lane must rescue a lexically-close example that dense misses."""
    from app.services import exemplar_store as es

    class FakeEmbed:
        async def aembed_query(self, text):
            # crude dense: only exact-token overlap wins
            return [1.0 if "GMV" in text else 0.0, 1.0]

    class FakeSession:
        async def execute(self, stmt):
            class R:
                def fetchall(self_inner):
                    return [("各服务区GMV是多少", "SELECT gmv ..."),
                            ("购物车加购件数", "SELECT SUM(sku_num) ...")]
            return R()

    async def fake_embed_cached(client, text):
        return await client.aembed_query(text)

    monkeypatch.setattr("app.core.embed_cache.embed_cached", fake_embed_cached)
    hits = await es.search(FakeSession(), FakeEmbed(),
                           "各服务区GMV是多少", top_k=1)
    assert hits and "GMV" in hits[0]["question"]
    assert "sparse" in hits[0]
