"""generate_sql 节点（P0+P1 优化版：DDL 增强 + 维度值注入 + 精简规则 + 多候选投票）"""
import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger
from app.core.metrics import SQL_GENERATED
from app.core.sql_dialect import get_dialect_info
from app.prompt.prompt_loader import load_prompt


def table_infos_to_ddl(table_infos: list) -> str:
    """增强版 DDL（含 REFERENCES + COMMENT + 示例值）"""
    if not table_infos:
        return "-- 无可用表"

    pk_map = {}
    pk_col_map = {}  # 表名 → 实际 PK 列名（用于 REFERENCES 精确生成）
    # 原始 PK 映射（列名 → 表名）
    raw_pk_map = {}
    for t in table_infos:
        for c in t.get("columns", []):
            if c.get("role") == "primary_key":
                raw_pk_map[c.get("name", "")] = t.get("name", "")
                pk_map[c.get("name", "")] = t.get("name", "")
                pk_col_map[t.get("name", "")] = c.get("name", "")

    # 智能推断 FK→PK 映射（xxx_id → dim_xxx.id）
    # 规则：user_id → dim_user_info.id, sku_id → dim_sku_info.id
    # 直接遍历所有 table_infos 避免 raw_pk_map["id"] 覆盖问题
    for t in table_infos:
        tbl_name = t.get("name", "")
        has_id_pk = any(c.get("role") == "primary_key" and c.get("name") == "id" for c in t.get("columns", []))
        if not has_id_pk:
            continue
        # dim_xxx 表的主键叫 id → 注册所有可能的 FK 变体
        suffix = tbl_name
        for prefix in ("dim_", "dws_", "dwd_", "ads_", "ods_"):
            if tbl_name.startswith(prefix):
                suffix = tbl_name[len(prefix):]
                break
        parts = suffix.split("_")
        if len(parts) >= 1:
            base = parts[0]
            fk_candidate = base + "_id"
            # dim > dws > 其他（dim 才是 FK 的正确 JOIN 目标）
            existing = pk_map.get(fk_candidate, "")
            if not existing:
                pk_map[fk_candidate] = tbl_name
            elif tbl_name.startswith("dim_") and not existing.startswith("dim_"):
                pk_map[fk_candidate] = tbl_name  # dim 优先于 dws/fact
            # 特殊规则
            if "province" in suffix:
                pk_map["province_id"] = tbl_name
            if "region" in suffix:
                pk_map["region_id"] = tbl_name
            if "coupon" in suffix:
                pk_map["coupon_id"] = tbl_name
            if "activity" in suffix:
                pk_map["activity_id"] = tbl_name
            if "order" in suffix and "detail" not in suffix:
                pk_map["order_id"] = tbl_name
            if "category" in suffix:
                if "1" in suffix:
                    pk_map["category1_id"] = tbl_name
                if "2" in suffix:
                    pk_map["category2_id"] = tbl_name
                if "3" in suffix:
                    pk_map["category3_id"] = tbl_name
            if "spu" in suffix and "sku" not in suffix:
                pk_map["spu_id"] = tbl_name
            if "trademark" in suffix:
                pk_map["tm_id"] = tbl_name

    ddl_parts = []
    for t in table_infos:
        tname = t.get("name", "?")
        role = t.get("role", "")
        desc = t.get("description", "")
        role_tag = "事实表" if role == "fact" else "维度表"

        fk_targets = set()
        for c in t.get("columns", []):
            if c.get("role") == "foreign_key":
                cname = c.get("name", "")
                target = pk_map.get(cname, "")
                if target and target != tname:
                    fk_targets.add(f"{cname}→{target}")
        header_parts = [desc if desc else tname, f"[{role_tag}]"]
        if fk_targets:
            header_parts.append(f"外键: {', '.join(list(fk_targets)[:3])}")

        lines = [f"-- {' | '.join(header_parts)}"]
        lines.append(f"CREATE TABLE {tname} (")

        col_lines = []
        for c in t.get("columns", []):
            cname = c.get("name", "?")
            ctype = c.get("type", "") or "VARCHAR(255)"
            crole = c.get("role", "")
            cdesc = c.get("description", "")
            calias = c.get("alias", [])
            cexamples = c.get("examples", [])

            col_def = f"  {cname} {ctype}"
            if crole == "primary_key":
                col_def += " PRIMARY KEY"
            if crole == "foreign_key":
                target = pk_map.get(cname, "")
                if target and target != tname:
                    target_pk = pk_col_map.get(target, cname)
                    col_def += f" REFERENCES {target}({target_pk})"""

            comment_parts = []
            role_tags_map = {
                "primary_key": "主键", "foreign_key": "外键",
                "measure": "度量(可聚合)", "dimension": "维度(可分组/过滤)",
            }
            rtag = role_tags_map.get(crole, "")
            if rtag:
                comment_parts.append(rtag)
            if cdesc:
                comment_parts.append(cdesc)
            if calias:
                comment_parts.append(f"同义词: {', '.join(str(a) for a in calias[:4])}")
            if cexamples and len(cexamples) <= 5:
                comment_parts.append(f"示例: {', '.join(str(e) for e in cexamples[:3])}")

            if comment_parts:
                col_def += f" COMMENT '{' | '.join(comment_parts)}'"
            col_lines.append(col_def)

        lines.append(",\n".join(col_lines))
        lines.append(");")
        ddl_parts.append("\n".join(lines))

    return "\n\n".join(ddl_parts)


def build_dimension_hint(state: DataAgentState) -> str:
    matched = state.get("matched_dimension_values", [])
    if not matched:
        return ""
    lines = ["【已知维度值（WHERE 必须用精确值）】"]
    for m in matched[:10]:
        col = m.get("column_name", "")
        val = m.get("value", "")
        tbl = m.get("table_id", "")
        if col and val:
            lines.append(f"- {tbl}.{col} = '{val}'")
    return "\n".join(lines) + "\n\n"


def build_glossary_hint(state: DataAgentState) -> str:
    glossary = state.get("glossary_matches", [])
    if not glossary:
        return ""
    lines = ["【术语映射】"]
    for g in glossary[:5]:
        lines.append(f"- '{g.get('term','')}' → {g.get('table_name','')}.{g.get('standard_name','')}")
    return "\n".join(lines) + "\n\n"


def build_feedback_hint(state: DataAgentState) -> str:
    feedback = state.get("feedback_examples", [])
    if not feedback:
        return ""
    lines = ["【历史参考】"]
    for f in feedback[:3]:
        lines.append(f"问: {f.get('query','')}\nSQL: {f.get('sql','')}")
    return "\n".join(lines) + "\n\n"


def _clean_sql(sql: str) -> str:
    """6重格式清理 (Vanna#187 SQLBot#725)"""
    bt = chr(96) * 3
    nl = chr(10)
    # 1. markdown code block
    if bt in sql:
        parts = sql.split(bt)
        sql = parts[1] if len(parts) > 1 else parts[0]
        for tag in ("sql" + nl, "json" + nl, "python" + nl):
            if sql.startswith(tag):
                sql = sql[len(tag):]
    # 2. strip quotes
    sql = sql.strip().strip(chr(34)).strip(chr(39))
    # 3. remove prefixes
    for pfx in ("SQL:", "sql:", "Answer:", "Result:"):
        if sql.startswith(pfx):
            sql = sql[len(pfx):].strip()
    # 4. remove trailing semicolons
    sql = sql.rstrip(";").strip()
    # 5. general strip
    sql = sql.strip()
    # 6. empty fallback - explicit error, no fake data
    if not sql:
        raise ValueError("LLM returned empty SQL - generation failed")
    return sql


async def _generate_one(chain, ddl, metrics, date_info, db_info, query) -> str:
    """生成一条候选 SQL"""
    sql = await chain.ainvoke({
        "ddl": ddl,
        "metrics": metrics,
        "date_info": date_info,
        "db_info": db_info,
        "query": query,
    })
    return _clean_sql(sql)


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "生成 SQL"})
    try:
        table_infos = state.get("table_infos", [])
        metric_infos = state.get("metric_infos", [])
        date_info = state.get("date_info", {})
        db_info = state.get("db_info", {})

        # P0-1: DDL
        ddl_str = table_infos_to_ddl(table_infos)
        metric_str = yaml.dump(metric_infos, allow_unicode=True, sort_keys=False) if metric_infos else "无"
        date_str = yaml.dump(date_info, allow_unicode=True, sort_keys=False) if date_info else "无"
        dialect_info = get_dialect_info(db_info.get("dialect", "doris")) if db_info else get_dialect_info("doris")
        db_str = dialect_info.get("hints", "Apache Doris dialect")

        # P0-2: 动态上下文
        dim_hint = build_dimension_hint(state)
        glossary_hint = build_glossary_hint(state)
        feedback_hint = build_feedback_hint(state)
        enriched_query = dim_hint + glossary_hint + feedback_hint + state["query"]

        # P1-2: 调试日志
        logger.info(f"DDL 输入({len(table_infos)}表): {ddl_str[:200]}")
        logger.info(f"enriched_query: {enriched_query[:150]}")

        prompt = PromptTemplate(
            template=load_prompt("generate_sql"),
            input_variables=["ddl", "metrics", "date_info", "db_info", "query"],
        )
        chain = prompt | llm | StrOutputParser()

        # P1-3: 多候选 SQL 投票（生成 2 条，选一致的）
        candidates = []
        for i in range(3):
            try:
                sql = await _generate_one(chain, ddl_str, metric_str, date_str, db_str, enriched_query)
                candidates.append(sql)
            except Exception as e:
                logger.warning(f"候选 {i+1} 生成失败: {e}")

        if not candidates:
            raise RuntimeError("所有候选 SQL 生成失败")

        # P1-3 投票（3条候选）：如果两条一致 → 直接用；不一致 → 选较短的非 "不存在" 的
        final_sql = candidates[0]
        if len(candidates) >= 2:
            if candidates[0] == candidates[1]:
                logger.info("两候选一致，直接采用")
            else:
                # 优先选非 "不存在" 的
                for c in candidates:
                    if "不存在" not in c and "message" not in c.lower():
                        final_sql = c
                        logger.info(f"投票：选非空候选 -> {c[:80]}")
                        break
                else:
                    # 都含"不存在"，取第一条
                    logger.info("两候选都返回'不存在'，取第一条")

        logger.info(f"最终 SQL: {final_sql[:120]}")
        SQL_GENERATED.inc()
        return {"sql": final_sql}
    except Exception as e:
        logger.error(f"生成 SQL 异常: {e}")
        raise
