"""filter_table node: LLM filters tables + columns + JSON fault tolerance + empty-table fallback"""
import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Filter Tables"})
    try:
        query = state["query"]
        table_infos = state.get("table_infos", [])
        original_tables = list(table_infos)
        names = [t.get("name","?") for t in original_tables]
        logger.info(f"filter_table received {len(original_tables)} tables: {names}")

        prompt = PromptTemplate(
            template=load_prompt("filter_table_info"),
            input_variables=["query", "table_infos"],
        )
        chain = prompt | llm | JsonOutputParser()

        # R2: JSON parsing fault tolerance (SQLBot#725)
        try:
            result = await chain.ainvoke({
                "query": query,
                "table_infos": yaml.dump(table_infos, allow_unicode=True, sort_keys=False),
            })
        except Exception as e:
            logger.warning(f"filter_table JSON parsing failed, keeping all tables: {e}")
            return {"table_infos": original_tables}

        # result: {table_name: [column_name]}
        # P1-2: table-level keyword re-injection - if LLM missed keyword-matched tables, force-add them
        keywords = state.get("keywords", [])
        if keywords:
            keyword_lower = set(k.lower() for k in keywords)
            for ti in original_tables:
                tn = ti.get("name", "")
                if tn not in result:
                    # Check if this table has columns matching the keywords
                    for c in ti.get("columns", []):
                        cn = c.get("name", "").lower()
                        ca = [a.lower() for a in c.get("alias", [])]
                        cd = c.get("description", "").lower()
                        texts = [cn] + ca + [cd]
                        if any(kw in txt or (txt and txt in kw) for kw in keyword_lower for txt in texts):
                            # Add this table to the result, including all columns matching the keywords
                            matched_cols = [c2["name"] for c2 in ti["columns"]
                                if any(kw in c2.get("name","").lower() or
                                       kw in c2.get("description","").lower() or
                                       any(kw in a.lower() for a in c2.get("alias",[]))
                                       for kw in keyword_lower)]
                            result[tn] = matched_cols or [c["name"] for c in ti["columns"]]
                            logger.info(f"Keyword re-injected table: {tn} (matched '{cn}')")
                            break

        # P1-3: column-level keyword re-injection - if LLM missed keyword-matched columns in the user query, force-keep them
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

        # P1-1: empty-table fallback
        if not table_infos:
            logger.warning("filter_table result is empty after filtering, falling back to pre-filter state")
            table_infos = original_tables

        logger.info(f"Tables after filtering: {[ti['name'] for ti in table_infos]} (LLM: {result})")
        return {"table_infos": table_infos}
    except Exception as e:
        logger.error(f"Filter tables error: {e}")
        return {"table_infos": state.get("table_infos", [])}
