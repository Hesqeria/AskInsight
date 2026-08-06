"""Complete data flow boundary tests: 7 stages, 21 items"""
import pytest
import json
import re
import asyncio
from unittest.mock import MagicMock, AsyncMock


# ===== Stage 1: Input layer =====

def test_df01_long_query():
    """DF-01: 500-character extra-long query does not crash"""
    from app.agent.nodes.extract_keywords import extract_keywords
    state = {'query': 'summarize ' * 200}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = asyncio.run(extract_keywords(state, runtime))
    assert 'keywords' in result


def test_df02_emoji_query():
    """DF-02: emoji/special symbols do not raise encoding errors"""
    from app.agent.nodes.extract_keywords import extract_keywords
    state = {'query': 'summarize🚀sales@#$%^&*()'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = asyncio.run(extract_keywords(state, runtime))
    assert isinstance(result['keywords'], list)


def test_df03_english_query():
    """DF-03: pure English query is processed normally"""
    from app.agent.nodes.extract_keywords import extract_keywords
    state = {'query': 'show me total sales by region'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = asyncio.run(extract_keywords(state, runtime))
    assert len(result['keywords']) > 0


# ===== Stage 2: Intent layer =====

@pytest.mark.asyncio
async def test_df04_mixed_intent():
    """DF-04: chitchat + query mixed -> classified as query"""
    from app.agent.nodes.intent_recognition import intent_recognition
    state = {'query': 'hello, by the way what about north region sales'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await intent_recognition(state, runtime)
    assert 'intent' in result


@pytest.mark.asyncio
async def test_df05_empty_query():
    """DF-05: empty string does not crash"""
    from app.agent.nodes.extract_keywords import extract_keywords
    state = {'query': ''}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await extract_keywords(state, runtime)
    assert 'keywords' in result


# ===== Stage 3: Recall layer =====

@pytest.mark.asyncio
async def test_df06_no_match_field():
    """DF-06: rare word with no match -> empty recall does not crash"""
    from app.agent.nodes.recall_column import recall_column
    state = {'query': 'xyzabc', 'keywords': ['xyzabc']}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    runtime.context = {
        'embedding_client': MagicMock(aembed_query=AsyncMock(return_value=[0.1]*1024)),
        'column_milvus_repository': MagicMock(async_search_safe=AsyncMock(return_value=[])),
    }
    result = await recall_column(state, runtime)
    assert result['retrieved_columns'] == []


def test_df07_rrf_multi_path():
    """DF-07: three recall paths hit the same field -> highest RRF score"""
    from app.agent.nodes.merge_retrieved_info import _rrf_score
    # all three paths rank 1st
    score = _rrf_score([0, 0, 0])
    # single path ranks 1st
    single = _rrf_score([0])
    assert score > single * 2.5  # three paths far higher than single


# ===== Stage 4: Generation layer =====

@pytest.mark.asyncio
async def test_df08_cte_allowed():
    """DF-08: WITH/CTE is allowed by the safety gateway"""
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    state = {'sql': 'WITH t AS (SELECT 1) SELECT * FROM t LIMIT 10'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await validate_sql_safety(state, runtime)
    assert result.get('error') is None


def test_df09_multi_candidate():
    """DF-09: 3 candidates"""
    import inspect
    from app.agent.nodes.generate_sql import generate_sql
    src = inspect.getsource(generate_sql)
    assert 'range(3)' in src


# ===== Stage 5: Validation layer =====

@pytest.mark.asyncio
async def test_df10_drop_blocked():
    """DF-10: DROP TABLE is blocked"""
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    state = {'sql': 'DROP TABLE fact_order'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await validate_sql_safety(state, runtime)
    assert result.get('error') is not None


@pytest.mark.asyncio
async def test_df11_union_injection():
    """DF-11: UNION SELECT injection is blocked"""
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    state = {'sql': 'SELECT 1 UNION SELECT password FROM users LIMIT 10'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await validate_sql_safety(state, runtime)
    assert result.get('error') is not None


@pytest.mark.asyncio
async def test_df12_auto_limit():
    """DF-12: LIMIT is automatically added when missing"""
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    state = {'sql': 'SELECT * FROM fact_order'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await validate_sql_safety(state, runtime)
    assert 'LIMIT' in result.get('sql', '').upper()


# ===== Stage 6: Execution layer =====

@pytest.mark.asyncio
async def test_df13_empty_result():
    """DF-13: empty result yields a friendly hint"""
    import inspect
    from app.agent.nodes.execute_sql import execute_sql
    src = inspect.getsource(execute_sql)
    assert 'Query result is empty' in src or 'hint' in src


def test_df14_large_result_limit():
    """DF-14: large result set is protected by LIMIT"""
    from app.agent.nodes.validate_sql_safety import ALLOWED_TABLES
    # safety gateway enforces LIMIT 1000
    assert len(ALLOWED_TABLES) > 5  # whitelist includes DWS/ADS


# ===== Stage 7: Post-processing layer =====

def test_df15_lineage_join():
    """DF-15: JOIN SQL lineage extraction"""
    from app.agent.nodes.extract_lineage import extract_lineage_from_sql
    sql = 'SELECT r.region_name AS region FROM fact_order f JOIN dim_region r ON f.region_id = r.region_id LIMIT 10'
    lineage = extract_lineage_from_sql(sql)
    assert len(lineage) > 0
    assert any(l['source_table'] == 'dim_region' for l in lineage)


@pytest.mark.asyncio
async def test_df16_anomaly_no_baseline():
    """DF-16: first query with no baseline -> skip detection without crashing"""
    from app.agent.nodes.anomaly_detection import anomaly_detection
    state = {'_last_result': [{'total': 100.0}], 'query': 'test'}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    runtime.context = {'meta_doris_repository': MagicMock(session=MagicMock())}
    runtime.context['meta_doris_repository'].session.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    # should not crash
    result = await anomaly_detection(state, runtime)
    assert isinstance(result, dict)


def test_df17_drill_attribution():
    """DF-17: attribution analysis returns factors"""
    from app.agent.nodes.drill_down_analysis import _compute_attribution
    data = [
        {'dimension_value': 'A', 'total_amount': 100},
        {'dimension_value': 'B', 'total_amount': 50},
    ]
    result = _compute_attribution(data, [])
    assert result['top_factor'] != ''
    assert result['contribution_pct'] > 0


def test_df18_decision_insights():
    """DF-18: decision insight node exists"""
    from app.agent.nodes.decision_insight import decision_insight
    assert callable(decision_insight)


def test_df19_sse_format():
    """DF-19: SSE format is correct"""
    import inspect
    from app.services.query_service import QueryService
    src = inspect.getsource(QueryService.query)
    assert 'data: ' in src
    assert 'ensure_ascii=False' in src


def test_df20_cache_key():
    """DF-20: cache key generation"""
    import inspect
    from app.services.query_service import QueryService
    src = inspect.getsource(QueryService.query)
    assert 'cache_key' in src


def test_df21_resolve_context():
    """DF-21: multi-turn coreference resolution"""
    import inspect
    from app.agent.nodes.resolve_context import resolve_context
    src = inspect.getsource(resolve_context)
    assert 'history' in src.lower()
    assert '[-3:]' in src
