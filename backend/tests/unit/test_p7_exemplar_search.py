"""Exemplar retrieval end-to-end with fakes (no live DB)."""
import asyncio

from app.services import exemplar_store


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, q, params=None):
        return _FakeResult(self._rows)

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _FakeEmbed:
    """'相似问题' vectors: shared prefix => high cosine."""

    def __init__(self):
        self.calls = []

    async def aembed_query(self, t):
        self.calls.append(t)
        base = [1.0, 0.0, 0.0] if "GMV" in t else [0.0, 1.0, 0.0]
        return base

    async def aembed_documents(self, ts):
        return [await self.aembed_query(t) for t in ts]


def test_search_ranks_and_filters():
    rows = [
        ("昨天GMV是多少", "SELECT SUM(gmv) ..."),
        ("用户留存率趋势", "SELECT retention ..."),
        ("上月GMV总额", "SELECT SUM(gmv) ...2"),
    ]
    emb = _FakeEmbed()
    hits = asyncio.run(exemplar_store.search(
        _FakeSession(rows), emb, "今天GMV是多少", top_k=2))
    assert len(hits) == 2
    assert hits[0]["score"] >= hits[1]["score"]
    assert "GMV" in hits[0]["question"]
    # question + 3 exemplar questions all embedded via cache path
    assert len(emb.calls) == 4


def test_search_empty_store():
    hits = asyncio.run(exemplar_store.search(
        _FakeSession([]), _FakeEmbed(), "任意问题"))
    assert hits == []
