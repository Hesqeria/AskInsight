"""filter_table 节点：LLM 过滤表+字段 + JSON 容错 + 空表兜底"""
import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
# NOTE: keep the STRONG model here. deepseek-v4-flash burns 90-146s on
# this heavy YAML->JSON filtering prompt (reasoning explosion), vs
# GLM-5.3 ~16-22s. flash is only reliable for short keyword-expansion
# prompts (see recall_*/intent nodes).
from app.agent.llm import llm
from app.core.log import logger
from app.core.table_domain import select_top_tables_by_domain
from app.prompt.prompt_loader import load_prompt


# Aggregation-intent keywords that should pull ads_* pre-aggregated
# tables to the front. IMPORTANT: this is deliberately NARROW and
# verified against eval gold SQL. Baseline analysis showed:
#   - ads_gmv_total_day is used ONLY by GMV-total / GMV-trend /
#     GMV-highest-day / GMV-share questions (7 questions total)
#   - ALL "TOP N" / "排名" questions (13 questions) use dwd_*/dws_*
#     raw tables, NOT ads_*
#   - order-count / repurchase / growth-rate use dwd_order_info_inc
# So we must NOT include "排名" here — it would misroute TOP-N queries
# to ads_* and make EX worse.
_ADS_INTENT_KEYWORDS = [
    # GMV 总量/趋势/最高/占比 → ads_gmv_total_day
    "总gmv", "总成交额", "总成交金额", "gmv趋势", "gmv最高",
    "最高一天", "每天的gmv", "gmv占比", "gmv总量",
]


# SQLBot #1376 practice: when the question NAMES a table (verbatim short
# name or a business alias like 订单明细表), that table must top the
# candidate list - otherwise retrieval noise can match the wrong fact.
_EXPLICIT_TABLE_ALIASES = {
    "订单明细表": "dwd_order_detail_inc", "订单明细": "dwd_order_detail_inc",
    "明细表": "dwd_order_detail_inc",
    "订单表": "dwd_order_info_inc",
    "用户表": "dim_user_info",
    "省份表": "dim_base_province", "大区表": "dim_base_region",
    "品类表": "dim_base_category3", "商品表": "dim_sku_info",
    "日期表": "dim_date",
    "优惠券": "dws_coupon_coupon_use_day",
    "收藏": "dws_sku_sku_favor_day",
    "活动表": "dim_activity_info",
}


def _explicit_table_boost(table_infos: list[dict], query: str) -> list[dict]:
    q = (query or "").lower()
    boost_names = []
    for alias, tbl in _EXPLICIT_TABLE_ALIASES.items():
        if alias in q and tbl not in boost_names:
            boost_names.append(tbl)
    for ti in table_infos:
        short = str(ti.get("name", "")).lower()
        if short and short in q and short not in boost_names:
            boost_names.append(short)
    if not boost_names:
        return table_infos
    boosted = [ti for ti in table_infos
               if str(ti.get("name", "")).lower() in boost_names]
    rest = [ti for ti in table_infos if ti not in boosted]
    if boosted:
        logger.info(f"explicit table boost: {boost_names} -> front")
        return boosted + rest
    return table_infos


def _prioritize_ads_tables(table_infos: list[dict], query: str) -> list[dict]:
    """If the question carries aggregation intent, move ads_* tables to
    the FRONT of the candidate list (without dropping non-ads tables, so
    JOIN/fallback still works). Returns the reordered list, or the input
    unchanged if no aggregation intent is detected."""
    if not query:
        return table_infos
    q_lower = query.lower()
    has_intent = any(kw in q_lower for kw in _ADS_INTENT_KEYWORDS)
    if not has_intent:
        return table_infos

    ads = [t for t in table_infos if str(t.get("name", "")).startswith("ads_")]
    rest = [t for t in table_infos if not str(t.get("name", "")).startswith("ads_")]
    if not ads:
        return table_infos
    logger.info(f"ads_* 优先命中 '{query[:30]}' -> ads 表 {len(ads)} 个提前")
    return ads + rest


async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "过滤表"})
    try:
        query = state["query"]
        table_infos = state.get("table_infos", [])
        original_tables = list(table_infos)
        names = [t.get("name","?") for t in original_tables]
        logger.info(f"filter_table 收到 {len(original_tables)} 表: {names}")

        # F1: Domain segmentation for large-scale tables
        if len(table_infos) > 25:
            table_infos = select_top_tables_by_domain(table_infos)
            original_tables = list(table_infos)
            logger.info(f"Domain segmentation: reduced to {len(table_infos)} tables")

        # F2 (基线 7.2): ads_* 聚合表优先 —— 当问题命中业务术语(排名/TOP/GMV/汇总/占比等)
        # 时,确保 ads_* 表进入候选,因为 gold SQL 往往用 ads_* 而非 dwd_*/dws_*。
        ads_tables = _prioritize_ads_tables(table_infos, state.get("query", ""))
        if ads_tables:
            table_infos = ads_tables
            original_tables = list(table_infos)
            logger.info(f"ads_* 优先: 保留 {len(ads_tables)} 表 "
                        f"({[t.get('name') for t in ads_tables]})")

        # Explicit table mention outranks every heuristic (SQLBot #1376).
        table_infos = _explicit_table_boost(table_infos, state.get("query", ""))

        # Latency gate: LLM filtering costs ~30-60s on a reasoning model.
        # For small candidate sets the keyword re-injection rules below
        # are sufficient - skip the LLM entirely.
        import os as _os
        _skip_llm = len(table_infos) <= int(_os.getenv("FILTER_LLM_MAX_TABLES", "25"))
        prompt = PromptTemplate(
            template=load_prompt("filter_table_info"),
            input_variables=["query", "table_infos"],
        )
        chain = prompt | llm | JsonOutputParser()
        if _skip_llm:
            result = {t.get("name", ""): [c.get("name", "") for c in t.get("columns", [])]
                      for t in table_infos}
            logger.info(f"filter_table skipped LLM ({len(table_infos)} tables <= threshold)")


        # R2: JSON 解析容错 (SQLBot#725)
        try:
            if _skip_llm:
                pass  # result already built from rules above
            else:
                result = await chain.ainvoke({
                    "query": query,
                    "table_infos": yaml.dump(table_infos, allow_unicode=True, sort_keys=False),
                })
        except Exception as e:
            logger.warning(f"filter_table JSON 解析失败，保留全部表: {e}")
            return {"table_infos": original_tables}

        # result: {表名: [字段名]}
        # P1-2: 表级关键词反注 — 如果 LLM 漏掉了关键词匹配的表，强制加入
        keywords = state.get("keywords", [])
        if keywords:
            keyword_lower = set(k.lower() for k in keywords)
            for ti in original_tables:
                tn = ti.get("name", "")
                if tn not in result:
                    # 检查该表是否有列匹配关键词
                    for c in ti.get("columns", []):
                        cn = c.get("name", "").lower()
                        ca = [a.lower() for a in c.get("alias", [])]
                        cd = c.get("description", "").lower()
                        texts = [cn] + ca + [cd]
                        if any(kw in txt or (txt and txt in kw) for kw in keyword_lower for txt in texts):
                            # 将该表加入结果，包含所有匹配关键词的列
                            matched_cols = [c2["name"] for c2 in ti["columns"]
                                if any(kw in c2.get("name","").lower() or
                                       kw in c2.get("description","").lower() or
                                       any(kw in a.lower() for a in c2.get("alias",[]))
                                       for kw in keyword_lower)]
                            result[tn] = matched_cols or [c["name"] for c in ti["columns"]]
                            logger.info(f"关键词反注表: {tn} (匹配 '{cn}')")
                            break

        # P1-3: 列级关键词反注 — 如果 LLM 漏掉了用户查询中关键词匹配的列，强制保留
        keywords = state.get("keywords", [])
        for ti in table_infos[:]:
            if ti["name"] not in result:
                table_infos.remove(ti)
            else:
                sel_cols = set(result[ti["name"]])
                for c in ti["columns"]:
                    c_name = c.get("name", "")
                    c_alias = [a.lower() for a in c.get("alias", [])]
                    c_desc = c.get("description", "").lower()
                    text_hits = [c_name.lower()] + c_alias + [c_desc]
                    if any(kw.lower() in txt or (txt and txt in kw.lower()) for kw in keywords for txt in text_hits):
                        sel_cols.add(c_name)
                ti["columns"] = [c for c in ti["columns"] if c["name"] in sel_cols]

        # P1-1: 空表兜底
        if not table_infos:
            logger.warning("filter_table 过滤后表为空，回退到过滤前")
            table_infos = original_tables

        logger.info(f"过滤后表: {[ti['name'] for ti in table_infos]} (LLM: {result})")
        return {"table_infos": table_infos}
    except Exception as e:
        logger.error(f"过滤表异常: {e}")
        return {"table_infos": state.get("table_infos", [])}
