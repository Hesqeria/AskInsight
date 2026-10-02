"""LiveSQLBench 评测入口。

在 SQLite 版 LiveSQLBench 上评测我们的 NL2SQL 能力(测 Agent/LLM 竞争力)。

用法:
    # 1) 数据就位(下载 livesqlbench-base-lite-sqlite 后):
    #    <data_root>/livesqlbench_data.jsonl
    #    <data_root>/databases/<db>.db

    # 2) LLM 直调评测(推荐,反映模型竞争力)
    python -m app.eval.run_livesqlbench --data-root <path> --adapter llm --limit 20

    # 3) Mock(验证框架,返回 sol_sql 应 100% 通过)
    python -m app.eval.run_livesqlbench --data-root <path> --adapter mock --limit 10

    # 4) 只跑 SELECT Query 类
    python -m app.eval.run_livesqlbench --data-root <path> --adapter llm --category Query

输出: 报告写入 <report_dir>/<run_id>.{md,json}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from .dataset.livesqlbench_loader import LiveSQLBenchLoader, LiveSQLBenchTask
from .evaluator.sqlite_ex import (
    SqliteBackend, SqliteVerdict, evaluate_sqlite, normalize_rows,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("livesqlbench")

PROMPT_TEMPLATE = """You are an expert SQLite SQL writer. Write a single SQL query that answers the user's question.

[Database Tables]
{tables_block}

[Database Schema (DDL)]
{ddl}

{column_meanings_block}
{hkb_block}
[Rules]
1. Use ONLY tables and columns that exist in the schema above, with the EXACT casing shown in the DDL (SQLite table/column names are case-sensitive).
2. When the question asks for aggregates (average/median/sum/count by group), use GROUP BY and consider window functions (ROW_NUMBER/PARTITION BY) for median/rank.
3. When JSON fields are involved, use json_extract(col, '$.key') or the -> / ->> operators.
4. For values comparison, prefer exact matches; handle NULL with IS NULL / COALESCE as appropriate.
5. Output ONLY the SQL statement, no explanation, no markdown fences.

[Examples]
Q: For each product category, compute the total sales and the number of orders.
SQL: SELECT category, SUM(amount) AS total_sales, COUNT(*) AS order_count FROM orders GROUP BY category;

Q: Find the median salary per department.
SQL: WITH ranked AS (SELECT dept, salary, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary) AS rn, COUNT(*) OVER (PARTITION BY dept) AS cnt FROM employees) SELECT dept, AVG(salary) AS median_salary FROM ranked WHERE rn IN ((cnt+1)/2, (cnt+2)/2) GROUP BY dept;

Q: Get the top 3 customers by total spending.
SQL: SELECT customer_id, SUM(amount) AS total_spend FROM orders GROUP BY customer_id ORDER BY total_spend DESC LIMIT 3;

[Question]
{query}

SQL:"""


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #
class LlmSqlAdapter:
    """直接用 LLM 生成 SQL(裸模型,作为 Agent 对比基线)."""

    name = "llm"

    def __init__(self):
        from app.agent.llm import llm
        self._llm = llm

    def build_prompt(self, task: LiveSQLBenchTask, ctx: dict) -> str:
        ddl = ctx.get("ddl", "")
        col_m = ctx.get("column_meanings", "")
        hkb = ctx.get("hkb", "")
        tables = ctx.get("tables", [])
        tables_block = ", ".join(tables) if tables else "(no table list)"
        col_block = f"[Column Meanings]\n{col_m}\n" if col_m else ""
        hkb_block = f"[External Knowledge (HKB)]\n{hkb}\n" if hkb else ""
        ek = task.raw.get("external_knowledge") if hasattr(task, "raw") else None
        if ek and not hkb_block:
            ek_text = ek if isinstance(ek, str) else _render_ek(ek)
            if ek_text:
                hkb_block = f"[External Knowledge]\n{ek_text}\n"
        return PROMPT_TEMPLATE.format(
            ddl=ddl or "(no schema provided)",
            tables_block=tables_block,
            column_meanings_block=col_block,
            hkb_block=hkb_block,
            query=task.query,
        )

    async def ask(self, task: LiveSQLBenchTask, ctx: dict) -> str:
        prompt = self.build_prompt(task, ctx)
        return await _ask_llm(self._llm, prompt)


# --------------------------------------------------------------------------- #
# Agent 适配器(schema linking + 精准上下文)
# --------------------------------------------------------------------------- #
class AgentSqlAdapter:
    """两步式 Agent:先用 rerank 做 schema linking 选出相关列,
    再只把相关表的 DDL 给 LLM(减少噪声,模拟 RAG 精准召回)."""

    name = "agent"

    def __init__(self, top_columns: int = 20):
        self.top_columns = top_columns
        from app.agent.llm import llm
        self._llm = llm

    def _extract_columns(self, ddl: str) -> list[dict]:
        """从 DDL 解析所有 (table, column) 候选."""
        cols = []
        current_table = ""
        for line in ddl.splitlines():
            line = line.strip()
            if line.upper().startswith("CREATE TABLE"):
                # CREATE TABLE "name" (
                import re as _re
                m = _re.search(r'CREATE TABLE\s+"?([\w]+)"?', line, _re.IGNORECASE)
                current_table = m.group(1) if m else ""
                continue
            if line.startswith(")"):
                current_table = ""
                continue
            if current_table and line and not line.upper().startswith(("PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT")):
                m = _re.match(r'"?(\w+)"?\s', line)
                if m:
                    cols.append({"table": current_table, "column": m.group(1)})
        return cols

    def build_prompt(self, task: LiveSQLBenchTask, ctx: dict,
                     relevant_ddl: str) -> str:
        col_m = ctx.get("column_meanings", "")
        hkb = ctx.get("hkb", "")
        col_block = f"[Column Meanings]\n{col_m}\n" if col_m else ""
        hkb_block = f"[External Knowledge (HKB)]\n{hkb}\n" if hkb else ""
        ek = task.raw.get("external_knowledge") if hasattr(task, "raw") else None
        if ek and not hkb_block:
            ek_text = ek if isinstance(ek, str) else _render_ek(ek)
            if ek_text:
                hkb_block = f"[External Knowledge]\n{ek_text}\n"
        return PROMPT_TEMPLATE.format(
            ddl=relevant_ddl or "(no schema provided)",
            tables_block=", ".join(ctx.get("tables", [])) or "(none)",
            column_meanings_block=col_block,
            hkb_block=hkb_block,
            query=task.query,
        )

    async def ask(self, task: LiveSQLBenchTask, ctx: dict) -> str:
        ddl = ctx.get("ddl", "")
        query = task.query

        # 1. 从 DDL 提取列候选
        cols = self._extract_columns(ddl)
        if not cols:
            return await _ask_llm(self._llm, self.build_prompt(task, ctx, ddl))

        # 2. 构造列文档(query 相关信号:列名 + 含义)
        col_meaning_map = {}
        cm = ctx.get("column_meanings", "")
        for line in cm.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                col_meaning_map[k.strip().lower()] = v.strip()

        docs = []
        for c in cols:
            key = f"{c['table']}.{c['column']}".lower()
            meaning = col_meaning_map.get(key, "")
            docs.append(f"{c['table']}.{c['column']}: {meaning}")

        # 3. rerank 精排(降级:无 rerank 则用关键词匹配)
        rerank_client = None
        try:
            from app.clients.rerank_client_manager import rerank_client_manager
            if rerank_client_manager.client is not None:
                rerank_client = rerank_client_manager.client
        except Exception:
            rerank_client = None

        if rerank_client is not None:
            try:
                scored = await rerank_client.arerank(query, docs, top_n=self.top_columns)
                top_idx = [d["index"] for d in scored[:self.top_columns]]
            except Exception:
                top_idx = list(range(min(self.top_columns, len(docs))))
        else:
            # 关键词匹配降级:含 query 词元的列优先
            q_lower = query.lower()
            scored_local = sorted(
                range(len(docs)),
                key=lambda i: sum(1 for w in q_lower.split()
                                  if w in docs[i].lower()),
                reverse=True,
            )
            top_idx = scored_local[:self.top_columns]

        # 4. 组装相关表的 DDL(只含被选中的列所在表)
        top_tables = set()
        for i in top_idx:
            if i < len(cols):
                top_tables.add(cols[i]["table"])
        relevant_ddl_lines = []
        in_table = False
        cur = ""
        for line in ddl.splitlines():
            lstrip = line.strip()
            if lstrip.upper().startswith("CREATE TABLE"):
                import re as _re
                m = _re.search(r'CREATE TABLE\s+"?([\w]+)"?', lstrip, _re.IGNORECASE)
                tname = m.group(1) if m else ""
                if tname in top_tables:
                    in_table = True
                    cur = line
                else:
                    in_table = False
                    cur = ""
                continue
            if in_table:
                cur += "\n" + line
                if lstrip.startswith(")"):
                    relevant_ddl_lines.append(cur)
                    in_table = False
        if not relevant_ddl_lines:
            relevant_ddl = ddl  # 兜底:全量
        else:
            relevant_ddl = "\n".join(relevant_ddl_lines)

        prompt = self.build_prompt(task, ctx, relevant_ddl)
        return await _ask_llm(self._llm, prompt)


async def _ask_llm(llm, prompt: str) -> str:
    """调用 LLM 并提取 SQL."""
    try:
        raw = await llm.ainvoke(prompt)
        text = raw.content if hasattr(raw, "content") else str(raw)
        return _extract_sql(text)
    except Exception as e:
        logger.warning(f"LLM 调用失败: {e}")
        return ""
    """直接用 LLM 生成 SQL(不做 Doris RAG,适配任意 SQLite 库)."""

    name = "llm"

    def __init__(self):
        from app.agent.llm import llm
        self._llm = llm

    def build_prompt(self, task: LiveSQLBenchTask, ctx: dict) -> str:
        ddl = ctx.get("ddl", "")
        col_m = ctx.get("column_meanings", "")
        hkb = ctx.get("hkb", "")
        tables = ctx.get("tables", [])
        tables_block = ", ".join(tables) if tables else "(no table list)"
        col_block = f"[Column Meanings]\n{col_m}\n" if col_m else ""
        hkb_block = f"[External Knowledge (HKB)]\n{hkb}\n" if hkb else ""
        # GT 合入后 external_knowledge 可能有值,作为额外上下文
        ek = task.raw.get("external_knowledge") if hasattr(task, "raw") else None
        if ek and not hkb_block:
            ek_text = ek if isinstance(ek, str) else _render_ek(ek)
            if ek_text:
                hkb_block = f"[External Knowledge]\n{ek_text}\n"
        return PROMPT_TEMPLATE.format(
            ddl=ddl or "(no schema provided)",
            tables_block=tables_block,
            column_meanings_block=col_block,
            hkb_block=hkb_block,
            query=task.query,
        )

    async def ask(self, task: LiveSQLBenchTask, ctx: dict) -> str:
        prompt = self.build_prompt(task, ctx)
        try:
            raw = await self._llm.ainvoke(prompt)
            text = raw.content if hasattr(raw, "content") else str(raw)
            return _extract_sql(text)
        except Exception as e:
            logger.warning(f"LLM 调用失败: {e}")
            return ""


def _extract_sql(text: str) -> str:
    """从 LLM 输出提取 SQL(去 markdown 代码块,截取到第一个 ';')."""
    if not text:
        return ""
    text = text.strip()
    # 去掉 ```sql ... ```
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # 取第一段完整 SQL
    lines = []
    for line in text.splitlines():
        if line.strip():
            lines.append(line)
        elif lines:
            break
    return "\n".join(lines).strip()


def _render_ek(ek) -> str:
    """把 external_knowledge 渲染成文本(可能是 list[str]/list[dict]/str)."""
    if isinstance(ek, str):
        return ek
    if isinstance(ek, list):
        parts = []
        for item in ek:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                label = item.get("name") or item.get("term") or item.get("id")
                desc = item.get("definition") or item.get("description")
                if label and desc:
                    parts.append(f"{label}: {desc}")
                elif desc:
                    parts.append(str(desc))
                else:
                    parts.append(str(item))
        return "\n".join(parts)
    if isinstance(ek, dict):
        return "\n".join(f"{k}: {v}" for k, v in ek.items())
    return str(ek)


# --------------------------------------------------------------------------- #
# 评测主流程
# --------------------------------------------------------------------------- #
async def run_eval(data_root: str, adapter_name: str, *,
                   category: str | None = None,
                   limit: int | None = None,
                   offset: int = 0,
                   report_dir: str | None = None,
                   ordered_default: bool = True,
                   concurrency: int = 4):
    loader = LiveSQLBenchLoader(data_root)
    tasks = loader.load_tasks(category=category, limit=limit, offset=offset)
    if not tasks:
        raise RuntimeError(f"没有加载到题目(数据根目录: {data_root})")

    logger.info(f"加载 {len(tasks)} 题, adapter={adapter_name}, "
                f"concurrency={concurrency}")

    if adapter_name == "mock":
        adapter = None  # mock 直接返回 sol_sql
    elif adapter_name == "llm":
        adapter = LlmSqlAdapter()
    elif adapter_name == "agent":
        adapter = AgentSqlAdapter()
    else:
        raise ValueError(f"未知 adapter: {adapter_name}")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    sem = asyncio.Semaphore(max(1, int(concurrency)))
    verdicts: list[SqliteVerdict] = []

    async def worker(task: LiveSQLBenchTask):
        """评测单题(Query 走 SQLite EX, Management 走 test_cases)."""
        async with sem:
            ctx = loader.build_context(task.database)
            db_file = ctx.get("db_file", "")
            if not db_file:
                return SqliteVerdict(
                    instance_id=task.instance_id, question=task.query,
                    category=task.category, difficulty=task.difficulty,
                    pred_sql="", sol_sql=task.sol_sql, ex_correct=False,
                    error=f"找不到数据库 {task.database}",
                )

            is_management = task.category == "Management"

            # 1. 生成 pred_sql(s)(Management 可能是多语句)
            if adapter_name == "mock":
                pred_sqls = _get_sol_candidates(task) or [task.sol_sql]
            else:
                pred_sql = await adapter.ask(task, ctx)
                pred_sqls = [pred_sql] if pred_sql else []
            if not pred_sqls:
                return SqliteVerdict(
                    instance_id=task.instance_id, question=task.query,
                    category=task.category, difficulty=task.difficulty,
                    pred_sql="", sol_sql=task.sol_sql, ex_correct=False,
                    error="pred_sql 为空",
                )

            # 2a. Management:执行 test_cases 判定
            if is_management:
                from app.eval.evaluator.management_ex import evaluate_management
                sol_candidates = _get_sol_candidates(task)
                test_case_code = (task.raw.get("test_cases") or [""])[0] \
                    if isinstance(task.raw.get("test_cases"), list) \
                    else str(task.raw.get("test_cases") or "")
                v = evaluate_management(
                    db_file,
                    instance_id=task.instance_id, question=task.query,
                    pred_sqls=pred_sqls, sol_sqls=sol_candidates,
                    test_case_code=test_case_code,
                )
                # 归一化为 SqliteVerdict 兼容(报告用)
                sv = SqliteVerdict(
                    instance_id=v.instance_id, question=v.question,
                    category=task.category, difficulty=task.difficulty,
                    pred_sql=pred_sqls[0] if pred_sqls else "",
                    sol_sql="; ".join(sol_candidates),
                    ex_correct=v.test_case_ok, error=v.error,
                    match_reason=v.detail,
                )
                return sv

            # 2b. Query:执行 + 比较(多条 sol 任一匹配)
            sol_candidates = _get_sol_candidates(task)
            with SqliteBackend(db_file) as backend:
                v = None
                for sol_sql in sol_candidates:
                    if not sol_sql or not str(sol_sql).strip():
                        continue
                    cand = evaluate_sqlite(
                        backend,
                        instance_id=task.instance_id, question=task.query,
                        pred_sql=pred_sqls[0], sol_sql=str(sol_sql),
                        category=task.category, difficulty=task.difficulty,
                        ordered=ordered_default,
                    )
                    if cand.ex_correct:
                        v = cand
                        break
                    if v is None:
                        v = cand
                if v is None:
                    v = SqliteVerdict(
                        instance_id=task.instance_id, question=task.query,
                        category=task.category, difficulty=task.difficulty,
                        pred_sql=pred_sqls[0], sol_sql=task.sol_sql,
                        ex_correct=False, error="无有效 sol_sql(GT 缺失?)",
                    )
            return v

    # 并发执行
    results = await asyncio.gather(*(worker(t) for t in tasks))
    for i, (task, v) in enumerate(zip(tasks, results), 1):
        verdicts.append(v)
        status = "✓" if v.ex_correct else "✗"
        logger.info(f"[{i}/{len(tasks)}] {task.instance_id} {status} "
                    f"rows: pred={v.pred_rows} sol={v.sol_rows} {v.error[:60]}")

    # 3. 汇总
    report = _build_report(verdicts, run_id, adapter_name, data_root)
    _save_report(report, run_id, report_dir)
    _print_report(report)
    return report


def _get_sol_candidates(task) -> list[str]:
    """从 task 提取所有 sol_sql 候选(兼容 str / list[str] / list[dict])."""
    sol = task.raw.get("sol_sql") if hasattr(task, "raw") else None
    if sol is None:
        sol = task.sol_sql
    if isinstance(sol, str):
        return [sol] if sol.strip() else []
    if isinstance(sol, list):
        out = []
        for item in sol:
            if isinstance(item, str) and item.strip():
                out.append(item)
            elif isinstance(item, dict):
                sql = item.get("sql") or item.get("pred_sql") or ""
                if sql:
                    out.append(sql)
        return out
    if isinstance(sol, dict):
        sql = sol.get("sql") or ""
        return [sql] if sql else []
    return []


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _build_report(verdicts: list[SqliteVerdict], run_id: str,
                  adapter_name: str, data_root: str) -> dict:
    total = len(verdicts)
    passed = sum(1 for v in verdicts if v.ex_correct)
    by_cat: dict[str, dict] = {}
    by_diff: dict[str, dict] = {}
    errors = []
    for v in verdicts:
        cat = v.category or "?"
        if cat not in by_cat:
            by_cat[cat] = {"total": 0, "passed": 0}
        by_cat[cat]["total"] += 1
        by_cat[cat]["passed"] += int(v.ex_correct)

        d = v.difficulty or "?"
        if d not in by_diff:
            by_diff[d] = {"total": 0, "passed": 0}
        by_diff[d]["total"] += 1
        by_diff[d]["passed"] += int(v.ex_correct)

        if not v.ex_correct:
            errors.append(v.to_dict())

    return {
        "run_id": run_id,
        "adapter": adapter_name,
        "data_root": str(data_root),
        "time": datetime.now().isoformat(),
        "total": total,
        "passed": passed,
        "success_rate": round(passed / total, 4) if total else 0.0,
        "by_category": {
            k: {**v, "rate": round(v["passed"] / v["total"], 4) if v["total"] else 0}
            for k, v in by_cat.items()
        },
        "by_difficulty": {
            k: {**v, "rate": round(v["passed"] / v["total"], 4) if v["total"] else 0}
            for k, v in by_diff.items()
        },
        "failures": errors,
    }


def _save_report(report: dict, run_id: str, report_dir: str | None = None):
    base = Path(report_dir) if report_dir else Path(__file__).parent / "runs"
    base.mkdir(parents=True, exist_ok=True)
    json_path = base / f"livesqlbench_{run_id}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"报告已保存: {json_path}")
    return json_path


def _print_report(report: dict):
    print("\n" + "=" * 70)
    print(f"  LiveSQLBench 评测报告  |  run={report['run_id']}  adapter={report['adapter']}")
    print("=" * 70)
    print(f"  总题数: {report['total']}")
    print(f"  通过: {report['passed']}/{report['total']} = {report['success_rate']:.1%}")
    print("\n  [按类别]")
    for k, v in report["by_category"].items():
        print(f"    {k:12s} {v['passed']:4d}/{v['total']:<4d} = {v['rate']:.1%}")
    print("\n  [按难度]")
    for k, v in report["by_difficulty"].items():
        print(f"    {k:12s} {v['passed']:4d}/{v['total']:<4d} = {v['rate']:.1%}")
    if report["failures"]:
        print(f"\n  失败 {len(report['failures'])} 题(前 5):")
        for f in report["failures"][:5]:
            print(f"    ✗ {f['instance_id']} [{f['category']}] {f['question'][:40]}")
            if f.get("error"):
                print(f"        → {f['error'][:80]}")
    print("=" * 70)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description="LiveSQLBench 评测")
    parser.add_argument("--data-root", required=True, help="数据集根目录")
    parser.add_argument("--adapter", default="llm", choices=["llm", "mock", "agent"])
    parser.add_argument("--category", default=None, choices=["Query", "Management"],
                        help="只测某类别")
    parser.add_argument("--limit", type=int, default=None, help="前 N 题")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--report-dir", default=None)
    parser.add_argument("--concurrency", type=int, default=4, help="并发数")
    parser.add_argument("--unordered", action="store_true",
                        help="结果无序比较(默认有序)")
    args = parser.parse_args()

    asyncio.run(run_eval(
        args.data_root, args.adapter,
        category=args.category, limit=args.limit, offset=args.offset,
        report_dir=args.report_dir, ordered_default=not args.unordered,
        concurrency=args.concurrency,
    ))


if __name__ == "__main__":
    main()
