"""Batch warm: one aembed_documents call fills the cache for all misses."""
import asyncio
import pytest

from app.core import embed_cache


class _FakeClient:
    def __init__(self):
        self.calls = 0

    async def aembed_documents(self, texts):
        self.calls += 1
        return [[0.1, 0.2] for _ in texts]

    async def aembed_query(self, text):
        raise AssertionError("should not be reached - cache must hit")


@pytest.fixture()
def fresh_cache(monkeypatch):
    """Swap in an empty cache dict so global state is untouched."""
    saved = dict(embed_cache._cache)
    monkeypatch.setattr(embed_cache, "_cache", {})
    yield
    embed_cache._cache.clear()
    embed_cache._cache.update(saved)


def test_warm_many_batches_and_fills(fresh_cache):
    async def run():
        c = _FakeClient()
        added = await embed_cache.warm_many(c, ["订单", "区域", "GMV", "订单"])
        assert added == 3  # dedupe
        assert c.calls == 1  # single batch call
        vec = await embed_cache.embed_cached(c, "订单")
        assert vec == [0.1, 0.2]

    asyncio.run(run())


def test_warm_many_no_miss_no_call(fresh_cache):
    async def run():
        c = _FakeClient()
        await embed_cache.warm_many(c, [])
        assert c.calls == 0

    asyncio.run(run())
