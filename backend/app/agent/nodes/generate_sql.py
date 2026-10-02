import asyncio
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
from app.ontology.plan import render_sql_from_plan
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
    for g in glossary[:8]:
        term = g.get('term', '')
        table = g.get('table_name', '')
        col = g.get('column_name') or g.get('standard_name', '')
        desc = g.get('description', '')
        # 含状态码/时间/规则描述的 term 带说明（desc 中常有 "已支付=order_status='1002'" 这类映射）
        if table:
            lines.append(f"- '{term}' → {table}.{col}")
        else:
            lines.append(f"- '{term}' → {desc}")
    return "\n".join(lines) + "\n\n"


def build_feedback_hint(state: DataAgentState) -> str:
    feedback = state.get("feedback_examples", [])
    if not feedback:
        return ""
    lines = ["【历史参考】"]
    for f in feedback[:3]:
        lines.append(f"问: {f.get('query','')}\nSQL: {f.get('sql','')}")
    return "\n".join(lines) + "\n\n"


def build_plan_hint(state: DataAgentState) -> str:
    """EQ007/008: inject ranking/period-over-period hints from
    semantic_plan into the LLM prompt so the LLM picks up ORDER BY LIMIT
    and LAG() window patterns even when confidence < 0.7 (LLM path)."""
    plan_dict = state.get("semantic_plan") or {}
    if not plan_dict:
        return ""
    notes = plan_dict.get("notes", []) or []
    order_by = plan_dict.get("order_by", []) or []
    limit = plan_dict.get("limit")
    hints = []
    if order_by:
        parts = []
        for o in order_by:
            parts.append(f"{o.get('column','')} {o.get('direction','DESC')}")
        hint = "【排名提示】问题含排名/Top-N 语义，SQL 必须包含 ORDER BY " + ", ".join(parts)
        if limit:
            hint += f" LIMIT {limit}"
        hints.append(hint + "，否则结果会返回多行而非最高/最低的那一条。")
    note_text = " ".join(notes)
    if "period-over-period" in note_text or "环比" in note_text or "同比" in note_text:
        hints.append(
            "【环比/同比提示】问题含环比/同比语义，使用 LAG() 窗口函数计算上期值，"
            "公式：(本期 - LAG(本期) OVER (ORDER BY dt)) / LAG(本期) OVER (ORDER BY dt) * 100。"
            "示例：SELECT (cur.gmv - prev.gmv) / prev.gmv * 100 AS growth_rate FROM "
            "(SELECT dt, SUM(gmv) AS gmv, LAG(SUM(gmv)) OVER (ORDER BY dt) AS prev_gmv "
            "FROM ads_gmv_total_day WHERE dt BETWEEN 上期起 AND 本期末 GROUP BY dt) t"
        )
    if not hints:
        return ""
    return chr(10).join(hints) + chr(10) + chr(10)


def _clean_sql(sql) -> str:
    """6重格式清理 (Vanna#187 SQLBot#725)"""
    if not isinstance(sql, str):
        raise ValueError(f"LLM 返回非字符串 SQL: type={type(sql).__name__}, value={str(sql)[:200]}")
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


async def _generate_one(chain, ddl, metrics, date_info, db_info, query,
                        exemplars="(none)", learnings="(none)") -> str:
    """生成一条候选 SQL"""
    sql = await chain.ainvoke({
        "ddl": ddl,
        "metrics": metrics,
        "date_info": date_info,
        "db_info": db_info,
        "query": query,
     "exemplars": exemplars, "learnings": learnings})
    return _clean_sql(sql)


def _dict_to_plan(d: dict):
    """Reconstruct a SemanticPlan from its dict form (state passes dicts).
    Delegates to SemanticPlan.from_dict (single source of truth)."""
    from app.ontology.plan import SemanticPlan
    return SemanticPlan.from_dict(d)




async def _persist_candidates(runtime, state, candidates, final_sql, chosen_by):
    """P3: 候选 SQL 留痕（PRD §6 一次准确率统计的数据源，best-effort）"""
    try:
        repo = runtime.context.get("meta_doris_repository")
        rid = state.get("_request_id", "")
        if repo is None or not rid or not candidates:
            return
        from datetime import datetime as _dt
        from sqlalchemy import text as _text
        for i, c in enumerate(candidates):
            await repo.session.execute(_text(
                "INSERT INTO data_agent.sql_candidate "
                "(request_id, cand_no, sql_text, chosen, chosen_by, created_at) "
                "VALUES (:rid, :no, :s, :ch, :cb, :ca)"),
                {"rid": rid, "no": i + 1, "s": c[:4000],
                 "ch": c == final_sql, "cb": chosen_by, "ca": _dt.now()})
        await repo.session.commit()
    except Exception as e:
        logger.warning(f"sql_candidate persistence skipped: {e}")


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "生成 SQL"})
    try:
        # --- P4 short-circuit: render from semantic_plan if grounded ---
        plan_dict = state.get("semantic_plan")
        if (plan_dict and plan_dict.get("confidence", 0) >= 0.7
                and (not plan_dict.get("pop", False)
                     # dual-period compare with materialized windows is
                     # deterministically rendered (UNION ALL) - no LLM
                     or plan_dict.get("pop_windows"))):
            try:
                plan = _dict_to_plan(plan_dict)
                sql = render_sql_from_plan(plan)
                logger.info(f"SemanticPlan rendered SQL (conf={plan.confidence}): {sql[:120]}")
                await _persist_candidates(runtime, state, [sql], sql, "plan_render")
                SQL_GENERATED.inc()
                from app.agent.events import emit
                emit("sql/generated", {"sql": sql[:800], "source": "plan_render"})
                # Renderer output is registry-grounded and deterministic -
                # tag it so assess_complexity never routes it into the
                # correct_sql degradation path (which used to rewrite a
                # correct 5-join query into a lossy simpler one).
                return {"sql": sql, "sql_source": "plan_render"}
            except Exception as e:
                logger.warning(f"plan render failed, falling back to LLM: {e}")

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
        plan_hint = build_plan_hint(state)
        enriched_query = dim_hint + glossary_hint + feedback_hint + plan_hint + state["query"]

        # M8: injection budget - estimate, trim by priority
        # (plan_hint > dimension > glossary > DDL > few-shot > history),
        # record context/pressure. Never trims the query itself.
        try:
            from app.core.token_meter import budget_parts, record_pressure
            _parts = {
                "ddl": ddl_str, "metrics": metric_str,
                "dimension": dim_hint, "glossary": glossary_hint,
                "few_shot": feedback_hint, "plan_hint": plan_hint,
                "query": state["query"],
            }
            _bounded, _trims = budget_parts(_parts)
            ddl_str = _bounded.get("ddl", ddl_str)
            dim_hint = _bounded.get("dimension", dim_hint)
            glossary_hint = _bounded.get("glossary", glossary_hint)
            feedback_hint = _bounded.get("few_shot", feedback_hint)
            plan_hint = _bounded.get("plan_hint", plan_hint)
            record_pressure("generate_sql", _parts, _bounded, _trims)
            if _trims:
                logger.info(
                    f"prompt budget trimmed: "
                    f"{[(t.part, t.before, t.after) for t in _trims]}"
                )
        except Exception as _e:
            logger.debug(f"token budget skipped (non-fatal): {_e}")

        # P1-2: 调试日志
        logger.info(f"DDL 输入({len(table_infos)}表): {ddl_str[:200]}")
        logger.info(f"enriched_query: {enriched_query[:150]}")

        # Vanna-style flywheel: few-shot exemplars of verified question-SQL
        # pairs (user corrections + successful executions).
        exemplars = ""
        try:
            from app.services import exemplar_store
            meta_repo = runtime.context.get("meta_doris_repository")
            import os as _os
            if meta_repo is not None and _os.getenv("ABLATION_NO_EXEMPLARS") != "1":
                hits = await exemplar_store.search(
                    meta_repo.session, runtime.context.get("embedding_client"),
                    enriched_query, top_k=2)
                exemplars = exemplar_store.format_for_prompt(hits)
                if hits:
                    logger.info(f"exemplar hits: {len(hits)} "
                                f"(top score {hits[0]['score']})")
        except Exception as ex_err:
            logger.debug(f"exemplar retrieval skipped: {ex_err}")
            exemplars = "(none)"

        # dash-style learnings: recent pitfalls the pipeline paid for.
        learnings = "(none)"
        try:
            from app.services import sql_learning_store
            import os as _os2
            meta_repo = runtime.context.get("meta_doris_repository")
            if meta_repo is not None and _os2.getenv("ABLATION_NO_EXEMPLARS") != "1":
                lrows = await sql_learning_store.recent_learnings(
                    meta_repo.session, limit=3)
                if lrows:
                    learnings = sql_learning_store.format_for_prompt(lrows)
                    logger.info(f"learnings injected: {len(lrows)} pitfalls")
        except Exception as le:
            logger.debug(f"learnings skipped: {le}")

        prompt = PromptTemplate(
            template=load_prompt("generate_sql"),
            input_variables=["ddl", "metrics", "date_info", "db_info", "query",
                             "exemplars", "learnings"],
        )
        chain = prompt | llm | StrOutputParser()

        # P1-3: 多候选 SQL 投票（生成 2 条，选一致的）
        # Latency gate: with a single reasoning model each candidate is
        # ~30-60s. Plan confidence >=0.7 means grounding is trustworthy
        # -> single candidate; ambiguous plans keep voting.
        try:
            _plan_d = state.get("semantic_plan") or {}
            _conf = float(_plan_d.get("confidence", 0) or 0)
        except Exception:
            _plan_d, _conf = {}, 0
        # pop plans: exemplars + rehydrated DDL support 2-candidate voting
        # (window SQL is slow to generate; the 3rd call rarely changes it)
        _n_cand = 1 if _conf >= 0.7 else (1 if _plan_d.get("pop") else 2)
        # Parallel candidate generation: serial xN cost 60-180s on the
        # main model; gathered it collapses to one candidate's latency.
        async def _safe_one():
            try:
                return await _generate_one(chain, ddl_str, metric_str,
                                           date_str, db_str, enriched_query,
                                           exemplars, learnings)
            except Exception as e:
                logger.warning(f"候选生成失败: {e}")
                return None

        _results = await asyncio.gather(
            *[_safe_one() for _ in range(_n_cand + 1)])
        candidates = [c for c in _results if c]

        if not candidates:
            raise RuntimeError("所有候选 SQL 生成失败")

        # P1-3 投票（3条候选）：如果两条一致 → 直接用；不一致 → 选较短的非 "不存在" 的
        final_sql = candidates[0]
        chosen_by = "single" if len(candidates) == 1 else "first"
        if len(candidates) >= 2:
            if candidates[0] == candidates[1]:
                chosen_by = "consensus"
                logger.info("两候选一致，直接采用")
            else:
                # 优先选非 "不存在" 的
                for c in candidates:
                    if "不存在" not in c and "message" not in c.lower():
                        if c != final_sql:
                            chosen_by = "first_nonempty"
                        final_sql = c
                        logger.info(f"投票：选非空候选 -> {c[:80]}")
                        break
                else:
                    # 都含"不存在"，取第一条
                    logger.info("两候选都返回'不存在'，取第一条")

        await _persist_candidates(runtime, state, candidates, final_sql, chosen_by)

        logger.info(f"最终 SQL: {final_sql[:120]}")
        SQL_GENERATED.inc()
        from app.agent.events import emit
        emit("sql/generated", {"sql": final_sql[:800], "source": "llm"})
        return {"sql": final_sql}
    except Exception as e:
        logger.error(f"生成 SQL 异常: {e}")
        raise
