"""B11.1-B11.2: concurrency"""
import asyncio
import uuid

import pytest


@pytest.mark.asyncio
async def test_b111_concurrent_10_queries_request_id_isolated():
    """B11.1: 10 concurrent request_id values are isolated"""
    from app.core.context import request_id_ctx_var

    async def task():
        rid = str(uuid.uuid4())
        request_id_ctx_var.set(rid)
        await asyncio.sleep(0.01)
        return request_id_ctx_var.get()

    rids = await asyncio.gather(*[task() for _ in range(10)])
    assert len(set(rids)) == 10  # 10 distinct ids


@pytest.mark.asyncio
async def test_b112_concurrent_100_extract_keywords_no_race():
    """B11.2: 100 concurrent extract_keywords calls have no race condition"""
    from app.agent.nodes.extract_keywords import extract_keywords
    from unittest.mock import MagicMock

    async def run_one(i):
        state = {"query": f"summarize sales amount {i}"}
        runtime = MagicMock()
        runtime.stream_writer = MagicMock()
        return await extract_keywords(state, runtime)

    results = await asyncio.gather(*[run_one(i) for i in range(100)])
    assert all("keywords" in r for r in results)
    assert len(results) == 100
