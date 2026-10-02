"""P1-08: Integrate multi-agent orchestrator into the /api/query main flow."""

import asyncio
import json
import time

from app.core.audit import send_audit_log
from app.core.cache import redis_cache
from app.core.context import request_id_ctx_var
from app.core.log import logger

ORCHESTRATOR_INTENTS = {
    "etl_request", "quality_check", "alert_investigate",
    "anomaly_explain", "metric_define", "metadata_query",
}

# Strong, action-oriented keywords that are unlikely to appear inside an
# ordinary data query. Substring matching previously hijacked queries
# like "各商品质量等级分布" (matched `quality_check` because of "质量")
# or "分析异常订单" (matched `anomaly_explain` because of "异常").
# Single generic morphemes ("质量", "异常", "下降", ...) are deliberately
# omitted; we require more specific phrases instead. The data-query
# fast-path (`_is_data_query`) is consulted BEFORE this list and handles
# the common analytical cases.
_KEYWORD_RULES = [
    ("etl_request", [
        "etl", "生成etl", "建仓", "建表", "建dws", "建一张",
        "数据管道", "数据同步任务", "跑数任务",
    ]),
    ("quality_check", [
        "数据质量", "质量检查", "质量规则", "完整性", "空值率",
    ]),
    ("alert_investigate", [
        "告警", "报警", "排查", "故障", "根因", "事故",
    ]),
    ("anomaly_explain", [
        "为什么暴涨", "为什么暴跌", "为什么骤降", "为什么下降",
        "为什么上涨", "暴涨原因", "暴跌原因", "骤降原因", "下降原因",
    ]),
    ("metric_define", [
        "定义指标", "新指标", "指标口径", "指标管理", "创建指标",
    ]),
    ("metadata_query", [
        "表结构", "有哪些表", "字段说明", "元数据", "血缘",
    ]),
]

_DATA_HINTS = [
    "多少", "统计", "求和", "平均值", "销售额", "gmv", "订单", "趋势",
    "对比", "哪个", "top", "排名", "明细", "报表", "数量", "分布", "占比",
    "总数", "计数",
]

# Hard ceiling for the optional LLM classification call. The underlying
# sync client may carry its own (much longer) timeout; we wrap with
# asyncio.wait_for so the event loop is never blocked beyond this.
_LLM_CLASSIFY_TIMEOUT_S = 3.0


def keyword_intent(query: str):
    q = query.lower()
    for intent, kws in _KEYWORD_RULES:
        for kw in kws:
            if kw in q:
                return intent
    return None


def _is_data_query(query: str):
    q = query.lower()
    return any(h in q for h in _DATA_HINTS)


async def classify_intent(query: str):
    """Return (intent, source) where source explains the decision
    ("data_hint" / "keyword" / "llm" / "llm_timeout" / "llm_error").
    The data-query fast-path is checked BEFORE keywords so ordinary
    analytical queries are never hijacked into the orchestrator."""
    # 1) Strong data-query signals short-circuit everything. This guards
    #    against false positives like "各商品质量等级分布" or "分析异常订单".
    if _is_data_query(query):
        return "data_query", "data_hint"

    # 2) Strong orchestrator keywords.
    kw = keyword_intent(query)
    if kw:
        return kw, "keyword"

    # 3) Otherwise ask the LLM, but never block the event loop: the LLM
    #    client is synchronous (httpx), so run it in a worker thread
    #    under a tight deadline. On any failure we default to data_query.
    try:
        intent = await asyncio.wait_for(
            _classify_intent_via_llm(query),
            timeout=_LLM_CLASSIFY_TIMEOUT_S,
        )
        return intent, "llm"
    except asyncio.TimeoutError:
        logger.warning(f"Intent classify LLM timed out after "
                       f"{_LLM_CLASSIFY_TIMEOUT_S}s, default data_query")
        return "data_query", "llm_timeout"
    except Exception as e:
        logger.warning(f"Intent classify LLM failed, default data_query: {e}")
        return "data_query", "llm_error"


async def _classify_intent_via_llm(query: str) -> str:
    def _call():
        from app.infra.llm_router import get_llm
        llm = get_llm()
        NL = chr(10)
        prompt = (
            "Classify the user query into exactly one intent." + NL +
            "Options: data_query | etl_request | quality_check | alert_investigate "
            "| anomaly_explain | metric_define | metadata_query | chat" + NL +
            "Query: " + query + NL + 'Return JSON {"intent": "..."}'
        )
        resp = llm.complete(
            [{"role": "user", "content": prompt}],
            task_type="intent", temperature=0)
        content = resp["content"] or ""
        if "```" in content:
            content = content.split("```")[1]
        if content[:6].lower().startswith("json"):
            content = content[content.index("{"):]
        data = json.loads(content)
        intent = data.get("intent", "data_query")
        if intent not in ORCHESTRATOR_INTENTS:
            return "data_query"
        return intent
    return await asyncio.to_thread(_call)


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
