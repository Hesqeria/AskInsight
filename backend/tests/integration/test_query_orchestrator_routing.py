"""Integration: /api/query routes orchestrator intents through the multi-agent hub."""
import os
import asyncio

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-integration-1234567890")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")

import pytest


@pytest.mark.asyncio
async def test_orchestrator_intent_routed_in_query_service():
    """Non-data-query intent (etl) is intercepted before the LangGraph runs."""
    from app.orchestrator.query_integration import (
        ORCHESTRATOR_INTENTS, classify_intent, keyword_intent,
    )
    intent, _ = await classify_intent("帮我建一个ETL数据管道")
    assert intent == "etl_request"
    assert intent in ORCHESTRATOR_INTENTS


@pytest.mark.asyncio
async def test_data_query_not_routed():
    from app.orchestrator.query_integration import classify_intent
    intent, _ = await classify_intent("上周GMV是多少")
    assert intent == "data_query"


@pytest.mark.asyncio
async def test_stream_sse_shape():
    from app.orchestrator.query_integration import stream_orchestrator
    lines = []
    async for line in stream_orchestrator("检查数据质量", "quality_check", username="u"):
        lines.append(line)
    assert lines
    assert lines[0].startswith("data: ")
    # ensure JSON payloads
    import json
    for ln in lines:
        payload = json.loads(ln.replace("data: ", "", 1))
        assert isinstance(payload, dict)
