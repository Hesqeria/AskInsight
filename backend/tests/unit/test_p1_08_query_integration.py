"""P1-08 query integration tests."""
import asyncio
import json


def test_keyword_intent_etl():
    from app.orchestrator.query_integration import keyword_intent
    assert keyword_intent("帮我生成一个ETL任务") == "etl_request"
    assert keyword_intent("建一张dws表") == "etl_request"


def test_keyword_intent_quality():
    from app.orchestrator.query_integration import keyword_intent
    assert keyword_intent("检查dws_gmv_daily的数据质量") == "quality_check"


def test_keyword_intent_alert():
    from app.orchestrator.query_integration import keyword_intent
    assert keyword_intent("排查告警根因") == "alert_investigate"
    assert keyword_intent("这个故障为什么发生") == "alert_investigate"


def test_keyword_intent_anomaly():
    from app.orchestrator.query_integration import keyword_intent
    assert keyword_intent("GMV为什么下降了") == "anomaly_explain"


def test_keyword_intent_metadata():
    from app.orchestrator.query_integration import keyword_intent
    assert keyword_intent("有哪些表") == "metadata_query"
    assert keyword_intent("查一下表结构") == "metadata_query"


def test_keyword_intent_metric():
    from app.orchestrator.query_integration import keyword_intent
    assert keyword_intent("定义一个新指标") == "metric_define"


def test_is_data_query():
    from app.orchestrator.query_integration import _is_data_query
    assert _is_data_query("昨天GMV是多少") is True
    assert _is_data_query("统计订单量") is True
    assert _is_data_query("你好") is False


def test_classify_intent_data_query_no_llm():
    from app.orchestrator.query_integration import classify_intent
    intent, source = asyncio.run(classify_intent("上周销售额是多少"))
    assert intent == "data_query"
    assert source == "data_hint"


def test_classify_intent_data_query_short_circuits_hijack():
    """Regression test: previously, queries like '各商品质量等级分布'
    or '分析异常订单' were hijacked by substring keyword matching into
    quality_check / anomaly_explain. The data-query fast-path must now
    route them to data_query instead."""
    from app.orchestrator.query_integration import classify_intent
    for q in ["各商品质量等级分布", "分析异常订单", "订单下降原因统计"]:
        intent, source = asyncio.run(classify_intent(q))
        assert intent == "data_query", f"{q!r} -> {intent} (expected data_query)"
        assert source == "data_hint"


def test_classify_intent_etl_no_llm():
    from app.orchestrator.query_integration import classify_intent
    intent, _ = asyncio.run(classify_intent("帮我建一个ETL数据管道"))
    assert intent == "etl_request"


def test_stream_orchestrator_yields_sse():
    from app.orchestrator.query_integration import stream_orchestrator
    lines = []
    async def collect():
        async for line in stream_orchestrator("帮我生成ETL", "etl_request", username="tester"):
            lines.append(line)
    asyncio.run(collect())
    assert len(lines) >= 2
    # first line stage
    first = json.loads(lines[0].replace("data: ", "", 1))
    assert "stage" in first
    # last line result
    last = json.loads(lines[-1].replace("data: ", "", 1))
    if "result" in last:
        assert last["result"][0]["intent"] == "etl_request"


def test_stream_orchestrator_quality():
    from app.orchestrator.query_integration import stream_orchestrator
    lines = []
    async def collect():
        async for line in stream_orchestrator("检查数据质量", "quality_check", username="tester"):
            lines.append(line)
    asyncio.run(collect())
    last = json.loads(lines[-1].replace("data: ", "", 1))
    assert last["result"][0]["data"]["quality"] == "normal"
