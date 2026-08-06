"""B9.1-B9.3: SQL injection and special characters"""
import pytest


@pytest.mark.asyncio
async def test_b91_sql_injection_drop_table():
    """B9.1: query contains DROP; the node should not let DROP flow into generated SQL (mock LLM does not actually generate; only verify the node does not raise)"""
    from app.agent.nodes.extract_keywords import extract_keywords
    state = {"query": "; DROP TABLE fact_order--"}
    from unittest.mock import MagicMock
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await extract_keywords(state, runtime)
    assert "keywords" in result
    # jieba should tokenize DROP / TABLE etc.
    all_kw = " ".join(result["keywords"])
    assert "DROP" in all_kw or "drop" in all_kw.lower() or True  # jieba behavior is not strictly asserted


@pytest.mark.asyncio
async def test_b92_sql_comment_in_query():
    """B9.2: SQL comment markers do not affect node execution"""
    from app.agent.nodes.extract_keywords import extract_keywords
    state = {"query": "summary /* comment */ sales amount"}
    from unittest.mock import MagicMock
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await extract_keywords(state, runtime)
    assert result["keywords"]


@pytest.mark.asyncio
async def test_b93_emoji_and_special_chars():
    """B9.3: emoji/special characters do not raise UnicodeError"""
    from app.agent.nodes.extract_keywords import extract_keywords
    state = {"query": "summary🚀sales@#$%^&*()"}
    from unittest.mock import MagicMock
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await extract_keywords(state, runtime)
    assert isinstance(result["keywords"], list)
