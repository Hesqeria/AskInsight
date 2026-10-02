"""评测主流程 + 报告生成

用法：
    python -m app.eval.run_eval --adapter mock --limit 5
    python -m app.eval.run_eval --adapter langgraph --limit 20
    python -m app.eval.run_eval --adapter mock --report-only runs/<run_id>.json
"""
import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pymysql

from .config import EvalConfig
from .evaluator.l3_ex import EXEvaluator, EvalResult
from .utils import (
    ast_similarity,
    schema_linking_f1,
    schema_linking_f1_columns,
    component_match,
    extract_referenced_tables,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("eval")


# =====================================================
# 1. Question Loader
# =====================================================
class QuestionLoader:
    """从 eval_questions 表加载题目"""

    def __init__(self, doris_config: dict):
        self.cfg = doris_config
        self._conn = pymysql.connect(
            host=self.cfg["host"], port=self.cfg["port"],
            user=self.cfg["user"], password=self.cfg["password"],
            database=self.cfg["meta_db"], charset="utf8mb4",
        )

    def load(self, limit: int | None = None) -> list[dict]:
        with self._conn.cursor(pymysql.cursors.DictCursor) as cur:
            sql = """SELECT question_id, question, gold_sql, entity,
                            sql_complexity, bird_difficulty
                     FROM eval_questions ORDER BY question_id"""
            if limit:
                sql += f" LIMIT {limit}"
            cur.execute(sql)
            return list(cur.fetchall())


# =====================================================
# 2. Run Saver（结果持久化）
# =====================================================
class RunSaver:
    """评测结果写入 eval_runs 表"""

    def __init__(self, doris_config: dict, run_id: str, model_name: str, prompt_version: str):
        self.conn = pymysql.connect(
            host=doris_config["host"], port=doris_config["port"],
            user=doris_config["user"], password=doris_config["password"],
            database=doris_config["meta_db"], charset="utf8mb4",
        )
        self.run_id = run_id
        self.model_name = model_name
        self.prompt_version = prompt_version

    def save(self, qid: str, result: EvalResult, intent: str = "query"):
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO eval_runs
                (run_id, question_id, pred_sql, level1_intent, level2_executable,
                 level3_ex, level4_llm_judge, latency_ms, model_name, prompt_version, error_msg)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (self.run_id, qid, result.pred_sql,
                 1 if intent == "query" else 0,
                 1 if result.level2_executable else 0,
                 1 if result.level3_ex else 0,
                 1 if result.level4_llm_judge else 0,
                 result.latency_ms,
                 self.model_name, self.prompt_version,
                 result.error_msg or ""))
        self.conn.commit()


# =====================================================
# 3. Report Generator
# =====================================================
class ReportGenerator:
    def __init__(self, results: list[dict], run_id: str, model_name: str):
        self.results = results
        self.run_id = run_id
        self.model_name = model_name

    def to_markdown(self) -> str:
        total = len(self.results)
        if total == 0:
            return "无结果"

        l2_ok = sum(1 for r in self.results if r["level2_executable"])
        l3_ok = sum(1 for r in self.results if r["level3_ex"])
        l4_ok = sum(1 for r in self.results if r.get("level4_llm_judge"))
        # final = L3 OR L4
        final_ok = sum(1 for r in self.results
                       if r["level3_ex"] or r.get("level4_llm_judge"))
        latencies = [r["latency_ms"] for r in self.results if r["latency_ms"] > 0]
        p50 = sorted(latencies)[len(latencies) // 2] if latencies else 0
        p95_idx = int(len(latencies) * 0.95)
        p95 = sorted(latencies)[min(p95_idx, len(latencies) - 1)] if latencies else 0

        # Extended metrics (PRD 必做三件套)
        ast_sims = [r.get("m3_ast_similarity") for r in self.results
                    if r.get("m3_ast_similarity") is not None]
        tbl_f1s = [r.get("m1_schema_f1_table") for r in self.results
                   if r.get("m1_schema_f1_table") is not None]
        col_f1s = [r.get("m1_schema_f1_col") for r in self.results
                   if r.get("m1_schema_f1_col") is not None]
        comp_overalls = [r.get("m2_component_overall") for r in self.results
                         if r.get("m2_component_overall") is not None]
        comp_selects = [r.get("m2_component_select") for r in self.results
                        if r.get("m2_component_select") is not None]
        comp_wheres = [r.get("m2_component_where") for r in self.results
                       if r.get("m2_component_where") is not None]
        avg = lambda xs: sum(xs) / len(xs) if xs else 0.0

        lines = []
        lines.append("=" * 60)
        lines.append(f"  掌柜问数 NL2SQL 评测报告 v2.0  (含扩展指标)")
        lines.append(f"  Run ID: {self.run_id}  |  Model: {self.model_name}")
        lines.append(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 60)
        lines.append("")

        # === 准确性 ===
        lines.append("【准确性】")
        lines.append(f"  L2 可执行:    {l2_ok}/{total} = {l2_ok/total*100:.1f}%")
        lines.append(f"  L3 EX:        {l3_ok}/{total} = {l3_ok/total*100:.1f}%")
        if any(r.get("level4_llm_judge") is not None for r in self.results):
            l3_fail = total - l3_ok
            if l3_fail > 0:
                lines.append(f"  L4 LLM Judge: +{l4_ok}/{l3_fail} = "
                             f"{l4_ok/l3_fail*100:.1f}% 兜底命中")
            else:
                lines.append(f"  L4 LLM Judge: 0/0 (无 L3 失败)")
            lines.append(f"  Final Pass:   {final_ok}/{total} = {final_ok/total*100:.1f}%")
        lines.append(f"  AST Sim avg:  {avg(ast_sims):.3f}")
        lines.append("")

        # === 结构性（新增）===
        lines.append("【结构性】")
        lines.append(f"  Schema Linking F1:")
        lines.append(f"    Table:  F1={avg(tbl_f1s):.3f}")
        lines.append(f"    Column: F1={avg(col_f1s):.3f}")
        lines.append(f"  Component Match:")
        lines.append(f"    select:   {avg(comp_selects):.3f}")
        lines.append(f"    where:    {avg(comp_wheres):.3f}")
        lines.append(f"    overall:  {avg(comp_overalls):.3f}")
        # Shortboard detection
        if comp_selects and avg(comp_selects) < 0.85:
            lines.append(f"    ⚠️ select 段偏低 → 检查 RAG 列召回")
        if comp_wheres and avg(comp_wheres) < 0.85:
            lines.append(f"    ⚠️ where 段偏低 → 检查 prompt WHERE 模板")
        if col_f1s and avg(col_f1s) < 0.80:
            lines.append(f"    ⚠️ 列召回 F1 偏低 → 加大 rerank top_n 或扩充 glossary")
        lines.append("")

        # === 效率性 ===
        lines.append("【效率性】")
        lines.append(f"  Latency:  P50={p50}ms  P95={p95}ms")
        if final_ok > 0 and latencies:
            lines.append(f"  Correct cost: ¥{(sum(latencies)/1000):.3f}s × N题/{final_ok} = "
                         f"{sum(latencies)/1000/final_ok:.3f}s/correct")
        lines.append("")

        # === 业务可用性（OPT-M8）===
        lines.append("【业务可用性】")
        # 意图识别准确率（L1）
        l1_pass = sum(1 for r in self.results if r.get("level1_intent"))
        if total > 0:
            lines.append(f"  意图识别准确率(L1): {l1_pass}/{total} = {l1_pass/total*100:.1f}%")
        # 失败可恢复（L4 兜底）
        l3_fail = total - l3_ok
        recoverable = l4_ok
        if l3_fail > 0:
            lines.append(f"  失败可恢复(L4兜底): {recoverable}/{l3_fail} = {recoverable/l3_fail*100:.1f}%")
        # SemanticPlan 短路占比（pred_sql 来自规则路径，无需 LLM）
        # 简化：latency<2000ms 视为短路（粗略）
        short_circuit = sum(1 for r in self.results
                            if 0 < r.get("latency_ms", 0) < 2000)
        lines.append(f"  SemanticPlan 短路: {short_circuit}/{total} = {short_circuit/total*100:.1f}%")
        lines.append("")

        # === 按业务实体 ===
        lines.append("【按业务实体】")
        from collections import defaultdict
        by_entity = defaultdict(lambda: [0, 0])
        for r in self.results:
            by_entity[r.get("entity", "?")][1] += 1
            if r["level3_ex"] or r.get("level4_llm_judge"):
                by_entity[r.get("entity", "?")][0] += 1
        for ent, (ok, tot) in sorted(by_entity.items(), key=lambda x: -x[1][1]):
            lines.append(f"  {ent:12s} {ok}/{tot} = {ok/tot*100:.1f}%")
        lines.append("")

        # === 按 SQL 复杂度 ===
        lines.append("【按 SQL 复杂度】")
        by_cplx = defaultdict(lambda: [0, 0])
        for r in self.results:
            by_cplx[r.get("sql_complexity", "?")][1] += 1
            if r["level3_ex"] or r.get("level4_llm_judge"):
                by_cplx[r.get("sql_complexity", "?")][0] += 1
        for cplx, (ok, tot) in sorted(by_cplx.items(), key=lambda x: -x[1][1]):
            lines.append(f"  {cplx:12s} {ok}/{tot} = {ok/tot*100:.1f}%")
        lines.append("")

        # === 失败案例 ===
        failures = [r for r in self.results
                    if not (r["level3_ex"] or r.get("level4_llm_judge"))]
        if failures:
            lines.append(f"【失败案例 {len(failures)} 题（部分展示）】")
            for r in failures[:10]:
                err = (r.get("error_msg") or r.get("match_reason")
                       or r.get("level4_reason") or "")[:80]
                lines.append(f"  {r['question_id']} [{r.get('sql_complexity','?')}/{r.get('entity','?')}] {r['question'][:30]}")
                # Show extended diagnostics if available
                tbl = r.get("m1_schema_f1_table")
                col = r.get("m1_schema_f1_col")
                ast = r.get("m3_ast_similarity")
                if tbl is not None or col is not None or ast is not None:
                    diag = f"tbl_f1={tbl} col_f1={col} ast={ast}"
                    lines.append(f"      [{diag}]")
                if err:
                    lines.append(f"      → {err}")
            lines.append("")

        # === 行动项（自动建议）===
        lines.append("【行动项】")
        actions = []
        if col_f1s and avg(col_f1s) < 0.80:
            actions.append(f"列召回 F1={avg(col_f1s):.2f} 是最大短板 → 加大 rerank top_n (30→50)")
        if comp_wheres and avg(comp_wheres) < 0.85:
            actions.append(f"WHERE 段 {avg(comp_wheres):.2f} 偏低 → 加 WHERE 模板到 prompt")
        if comp_selects and avg(comp_selects) < 0.85:
            actions.append(f"SELECT 段 {avg(comp_selects):.2f} 偏低 → 检查 SELECT 列选择逻辑")
        if ast_sims and avg(ast_sims) < 0.7 and final_ok / total > 0.7:
            actions.append(f"AST 均值 {avg(ast_sims):.2f} 偏低但 EX 高 → SQL 写法发散,统一 prompt 风格")
        if not actions:
            actions.append("✅ 所有结构性指标均在阈值内,无需立即行动")
        for i, a in enumerate(actions, 1):
            lines.append(f"  {i}. {a}")
        lines.append("")

        return "\n".join(lines)


# =====================================================
# 4. 主流程
# =====================================================
async def run_eval(cfg: EvalConfig, adapter_name: str, limit: int | None = None):
    run_id = cfg.run_id or datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    logger.info(f"=== 启动评测 === run_id={run_id} adapter={adapter_name} limit={limit}")

    doris_cfg = {
        "host": cfg.doris_host, "port": cfg.doris_port,
        "user": cfg.doris_user, "password": cfg.doris_password,
        "meta_db": cfg.doris_meta_db, "data_db": cfg.doris_data_db,
        "float_rtol": cfg.float_rtol,
    }

    # 1. 加载题目
    loader = QuestionLoader(doris_cfg)
    questions = loader.load(limit)
    logger.info(f"加载题目 {len(questions)} 题")

    # 2. 构造 adapter
    if adapter_name == "mock":
        from .adapter.mock import MockGoldAdapter
        adapter = MockGoldAdapter()
        for q in questions:
            adapter.set_gold(q["question_id"], q["gold_sql"])
    elif adapter_name == "mock_noise":
        from .adapter.mock import MockNoiseAdapter
        adapter = MockNoiseAdapter()
        for q in questions:
            adapter.set_gold(q["question_id"], q["gold_sql"])
    elif adapter_name == "mock_fail":
        from .adapter.mock import MockFailureAdapter
        adapter = MockFailureAdapter()
    elif adapter_name == "mock_equiv":
        from .adapter.mock import MockEquivalentAdapter
        adapter = MockEquivalentAdapter()
        for q in questions:
            adapter.set_gold(q["question_id"], q["gold_sql"])
    elif adapter_name == "langgraph":
        from .adapter.direct_langgraph import DirectLangGraphAdapter
        adapter = await DirectLangGraphAdapter(mock_date=cfg.mock_date).__aenter__()
    else:
        raise ValueError(f"未知 adapter: {adapter_name}")

    # 3. 构造评测器
    evaluator = EXEvaluator(doris_cfg)
    saver = RunSaver(doris_cfg, run_id, cfg.model_name, cfg.prompt_version)

    # 3a. 构造 LLM Judge（可选）
    llm_judge = None
    if cfg.enable_llm_judge:
        from .evaluator.l4_llm_judge import LLMJudge
        llm_judge = LLMJudge(threshold=cfg.llm_judge_threshold)
        logger.info(f"LLM Judge 启用: model={llm_judge.model} threshold={cfg.llm_judge_threshold}")
    else:
        logger.info("LLM Judge 关闭（如需开启 --enable-judge）")

    # 4. 逐题评测
    all_results = []
    for i, q in enumerate(questions, 1):
        qid = q["question_id"]
        logger.info(f"[{i}/{len(questions)}] {qid} {q['question'][:50]}")

        try:
            # 调 adapter
            if hasattr(adapter, "ask"):
                import inspect
                sig = inspect.signature(adapter.ask)
                if "question_id" in sig.parameters:
                    agent_resp = await adapter.ask(q["question"], question_id=qid)
                else:
                    agent_resp = await adapter.ask(q["question"])
            else:
                agent_resp = await adapter.ask(q["question"])

            # 评测
            result = evaluator.evaluate(
                question_id=qid,
                gold_sql=q["gold_sql"],
                pred_sql=getattr(agent_resp, "pred_sql", "") or (agent_resp.get("pred_sql", "") if isinstance(agent_resp, dict) else ""),
            )
            result.level1_intent = (getattr(agent_resp, "intent", "") == "query" or
                                    (agent_resp.get("intent", "") == "query" if isinstance(agent_resp, dict) else True))

            # L3 失败时调 L4
            if not result.level3_ex and llm_judge and result.pred_result is not None:
                gold_result, _ = evaluator._execute(q["gold_sql"])
                verdict = llm_judge.judge(
                    gold_sql=q["gold_sql"],
                    pred_sql=result.pred_sql,
                    gold_result=gold_result,
                    pred_result=result.pred_result,
                    pred_error=result.error_msg,
                )
                result.level4_llm_judge = verdict["equivalent"]
                result.level4_confidence = verdict["confidence"]
                result.level4_reason = verdict["reason"]
                result.recompute_final()
                logger.info(f"    L4 Judge: {verdict['equivalent']} (conf={verdict['confidence']:.2f}) {verdict['reason'][:60]}")

            status = "✓" if result.final_pass else "✗"
            extra = ""
            if result.level4_llm_judge and not result.level3_ex:
                extra = " (L4 兜底)"
            logger.info(f"    {status} L2={result.level2_executable} L3={result.level3_ex}{extra} {(result.match_reason or result.error_msg)[:50]}")

            # ---- Extended metrics (NL2SQL其他测评方法-PRD.md 必做三件套) ----
            try:
                gold_tables = extract_referenced_tables(q["gold_sql"])
                pred_tables = extract_referenced_tables(result.pred_sql or "")
                tbl_f1 = schema_linking_f1(gold_tables, pred_tables)
                col_f1 = schema_linking_f1_columns(q["gold_sql"], result.pred_sql or "")
                ast_sim = ast_similarity(q["gold_sql"], result.pred_sql or "")
                comp = component_match(q["gold_sql"], result.pred_sql or "")
            except Exception as e:
                logger.debug(f"extended metric error: {e}")
                tbl_f1 = col_f1 = type("X", (), {"f1": 0.0})()
                ast_sim = 0.0
                comp = None

        except Exception as e:
            result = EvalResult(question_id=qid, error_msg=f"评测异常: {str(e)[:200]}")
            logger.error(f"    ✗ 异常: {e}")
            tbl_f1 = type("X", (), {"f1": 0.0})()
            col_f1 = type("X", (), {"f1": 0.0})()
            ast_sim = 0.0
            comp = None

        # 持久化
        saver.save(qid, result, intent="query")

        # 累积结果（含题目元数据 + 扩展指标）
        all_results.append({
            **result.to_dict(),
            "question": q["question"],
            "entity": q["entity"],
            "sql_complexity": q["sql_complexity"],
            "bird_difficulty": q["bird_difficulty"],
            "match_reason": result.match_reason,
            "level4_reason": result.level4_reason,
            # Extended (PRD 必做三件套)
            "m1_schema_f1_table": round(tbl_f1.f1, 4),
            "m1_schema_f1_col": round(col_f1.f1, 4),
            "m3_ast_similarity": round(ast_sim, 4),
            "m2_component_overall": round(comp.overall, 4) if comp else None,
            "m2_component_select": round(comp.select, 4) if comp else None,
            "m2_component_where": round(comp.where, 4) if comp else None,
        })

    # 5. 关闭 adapter（如有）
    if adapter_name == "langgraph":
        await adapter.__aexit__(None, None, None)

    # 6. 输出报告
    report_dir = cfg.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    json_path = report_dir / f"{run_id}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"run_id": run_id, "model": cfg.model_name, "results": all_results}, f, ensure_ascii=False, indent=2, default=str)

    # Markdown
    report = ReportGenerator(all_results, run_id, cfg.model_name).to_markdown()
    md_path = report_dir / f"{run_id}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)

    print("\n" + report)
    print(f"\n报告已保存: {md_path}")
    print(f"原始数据: {json_path}")


# =====================================================
# 5. CLI
# =====================================================
def main():
    parser = argparse.ArgumentParser(description="NL2SQL 评测脚本")
    parser.add_argument("--adapter", default="mock",
                        choices=["mock", "mock_noise", "mock_fail", "mock_equiv", "langgraph"],
                        help="适配器类型")
    parser.add_argument("--limit", type=int, default=None, help="题目数（默认全部）")
    parser.add_argument("--model", default="qwen3-vl-27b", help="模型名（用于报告）")
    parser.add_argument("--prompt-version", default="v3", help="Prompt 版本")
    parser.add_argument("--run-id", default=None, help="指定 run_id")
    parser.add_argument("--enable-judge", action="store_true",
                        help="启用 L4 LLM-as-Judge（L3 失败时调 LLM 兜底）")
    parser.add_argument("--judge-threshold", type=float, default=0.7,
                        help="LLM Judge 置信度阈值")
    parser.add_argument("--mock-date", default="2026-08-04",
                        help="Mock 当前日期（默认 dw 数据冻结日 2026-08-04）")
    args = parser.parse_args()

    cfg = EvalConfig(
        model_name=args.model,
        prompt_version=args.prompt_version,
        run_id=args.run_id or "",
        enable_llm_judge=args.enable_judge,
        llm_judge_threshold=args.judge_threshold,
        mock_date=args.mock_date,
    )

    asyncio.run(run_eval(cfg, args.adapter, args.limit))


if __name__ == "__main__":
    main()
