"""B12.1-B12.2: network jitter simulation"""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_b121_embedding_intermittent_failure():
    """B12.1: embedding fails intermittently; recall_column should fall back without raising"""
    from app.agent.nodes.recall_column import recall_column

    call_count = {"n": 0}

    async def flaky_embed(text):
        call_count["n"] += 1
        if call_count["n"] % 2 == 0:
            raise ConnectionError("simulated network jitter")
        return [0.1] * 1024

    state = {"query": "x", "keywords": ["x"]}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    runtime.context = {
        "embedding_client": MagicMock(aembed_query=flaky_embed),
        "column_milvus_repository": MagicMock(search_safe=lambda e: []),
    }
    # node should swallow the exception and return empty recall
    result = await recall_column(state, runtime)
    assert "retrieved_columns" in result


@pytest.mark.asyncio
async def test_b122_doris_pool_full_returns_error():
    """B12.2: Doris connection pool full; validate_sql should return error without hanging"""
    from app.agent.nodes.validate_sql import validate_sql
    state = {"sql": "SELECT 1"}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()

    async def slow_validate(sql):
        raise TimeoutError("pool exhausted")

    runtime.context = {"dw_doris_repository": MagicMock(validate_sql=slow_validate)}
    result = await validate_sql(state, runtime)
    assert result["error"] is not None
