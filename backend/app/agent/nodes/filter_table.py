"""filter_table 节点：LLM 过滤表+字段 + JSON 容错 + 空表兜底"""
import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger
from app.core.table_domain import select_top_tables_by_domain
from app.prompt.prompt_loader import load_prompt


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

        prompt = PromptTemplate(
            template=load_prompt("filter_table_info"),
            input_variables=["query", "table_infos"],
        )
        chain = prompt | llm | JsonOutputParser()

        # R2: JSON 解析容错 (SQLBot#725)
        try:
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
