"""P1-08: Integrate multi-agent orchestrator into the /api/query main flow."""

import json
import time
import re
import hashlib

from app.core.audit import send_audit_log
from app.core.cache import redis_cache
from app.core.context import request_id_ctx_var
from app.core.log import logger

ORCHESTRATOR_INTENTS = {
    "etl_request", "quality_check", "alert_investigate",
    "anomaly_explain", "metric_define", "metadata_query",
}

_KEYWORD_RULES = [
    ("etl_request", ["etl", "建仓", "建表", "建dws", "建一张", "生成etl", "数据管道", "数据同步任务", "跑数任务"]),
    ("quality_check", ["质量", "数据质量", "质量检查", "完整性", "空值率", "质量规则"]),
    ("alert_investigate", ["告警", "报警", "排查", "故障", "根因", "为什么挂了", "事故"]),
    ("anomaly_explain", ["异常", "波动", "下降", "下跌", "暴涨", "暴跌", "骤降", "为什么降", "下降原因"]),
    ("metric_define", ["定义指标", "新指标", "指标口径", "指标管理", "创建指标"]),
    ("metadata_query", ["表结构", "有哪些表", "有哪些表", "字段", "元数据", "血缘", "字段说明"]),
]


def keyword_intent(query: str):
    q = query.lower()
    for intent, kws in _KEYWORD_RULES:
        for kw in kws:
            if kw in q:
                return intent
    return None


def _is_data_query(query: str):
    data_hints = ["多少", "统计", "求和", "平均值", "销售额", "gmv", "订单", "趋势",
                  "对比", "哪个", "top", "排名", "明细", "报表", "数量"]
    q = query.lower()
    return any(h in q for h in data_hints)


async def classify_intent(query: str):
    kw = keyword_intent(query)
    if kw:
        return kw, ""
    if _is_data_query(query):
        return "data_query", ""
    try:
        from app.infra.llm_router import get_llm
        llm = get_llm()
        NL = chr(10)
        prompt = (
            "Classify the user query into exactly one intent." + NL +
            "Options: data_query | etl_request | quality_check | alert_investigate "
            "| anomaly_explain | metric_define | metadata_query | chat" + NL +
            "Query: " + query + NL + "Return JSON {\"intent\": \"...\"}"
        )
        resp = llm.complete(
            [{"role": "user", "content": prompt}],
            task_type="intent", temperature=0)
        content = resp["content"]
        if "```" in content:
            content = content.split("```")[1]
        if "json" in content[:6]:
            content = content[content.index("{") :]
        data = json.loads(content)
        intent = data.get("intent", "chat")
        if intent not in ORCHESTRATOR_INTENTS:
            return intent, ""
        return intent, ""
    except Exception as e:
        logger.warning(f"Intent classify LLM failed, default data_query: {e}")
        return "data_query", ""


async def stream_orchestrator(query: str, intent: str, username: str = "anonymous"):
    request_id = request_id_ctx_var.get()
    start_time = time.time()
    status = "error"
    result_rows = 0
    try:
        from app.orchestrator.factory import get_wired_registry
        from app.orchestrator.multi_agent import get_orchestrator

        reg = get_wired_registry()
        orch = get_orchestrator()
        orch.registry = reg

        request = {"query_id": request_id, "user_id": username, "role": "L2_analyst"}
        req_intent = {"intent": intent, "query": query, "text": query,
                      "metric": None, "table": None}

        yield _sse({"stage": "Orchestrator Dispatch"})
        dispatch = orch.dispatch(request, req_intent)
        yield _sse({"stage": "Agent: " + dispatch.primary_agent})

        result = await orch.execute_dispatch(dispatch, request, req_intent)
        if isinstance(result.data, dict) and result.data:
            result_rows = 1
        payload = [{"orchestrator": True, "intent": intent,
                    "primary_agent": dispatch.primary_agent,
                    "mode": dispatch.mode, "data": result.data,
                    "ok": result.ok, "error": result.error}]
        yield _sse({"result": payload})
        status = "success" if result.ok else "error"
    except Exception as e:
        logger.error(f"Orchestrator stream exception: {e}", exc_info=True)
        yield _sse({"error": str(e)})
        status = "error"
    finally:
        latency = int((time.time() - start_time) * 1000)
        await send_audit_log(request_id, username, query, sql="",
                             status=status, latency_ms=latency, result_rows=result_rows)


def _sse(obj) -> str:
    NL = chr(10)
    return "data: " + json.dumps(obj, ensure_ascii=False, default=str) + NL + NL
