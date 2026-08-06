"""B10.1-B10.2: extra-long input"""
import pytest
import time


@pytest.mark.asyncio
async def test_b101_10kb_query():
    """B10.1: 10KB query does not time out (completes within 5s)"""
    from app.agent.nodes.extract_keywords import extract_keywords
    state = {"query": "summarize sales amount " + "x" * 10000}
    from unittest.mock import MagicMock
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    start = time.time()
    result = await extract_keywords(state, runtime)
    elapsed = time.time() - start
    assert elapsed < 5, f"jieba processing timed out: {elapsed:.2f}s"
    assert "keywords" in result


def test_b102_large_table_infos_yaml_dump():
    """B10.2: yaml.dump of 100 tables does not raise (no real LLM call)"""
    import yaml
    tables = [{"name": f"t{i}", "role": "dim",
               "description": f"desc {i}",
               "columns": [{"name": "c", "type": "int", "role": "dimension",
                           "examples": [1], "description": "c", "alias": []}]}
              for i in range(100)]
    dumped = yaml.dump(tables, allow_unicode=True, sort_keys=False)
    assert len(dumped) > 0
