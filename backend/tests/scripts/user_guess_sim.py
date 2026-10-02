"""模拟用户猜想测试用例集 —— 遍历 NL → 候选生成 → SQL 执行验证。

覆盖真实用户的口语化/模糊/歧义问法,针对每种:
  1. 生成多路候选方案(含中文解释 + SQL 预览)
  2. 对每个候选 SQL 尝试在 Doris 实际执行
  3. 输出:候选数 / 置信度 / 可执行率 / 是否有"缺 join"等结构问题

用法:
  python tests/scripts/user_guess_sim.py [--verify-doris]
"""
import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.agent.candidate_generator import generate_candidates


# --------------------------------------------------------------------------- #
# 用户猜想用例集
# --------------------------------------------------------------------------- #
USER_GUESS_CASES = [
    # (id, 问题, 期望指标关键词, 备注)
    ("U01", "看下销售", {"GMV"}, "完全模糊 - 无指标/时间/维度"),
    ("U02", "最近生意怎么样", {"GMV", "order_count"}, "完全模糊 - 口语化"),
    ("U03", "用户活跃情况", {"DAU"}, "指标歧义 - 可能是DAU/MAU/复购"),
    ("U04", "华北的GMV", {"GMV"}, "缺时间"),
    ("U05", "上个月的销售", {"GMV"}, "缺维度/分组"),
    ("U06", "华北 上个月的gmv", {"GMV"}, "完整信息"),
    ("U07", "昨天生意咋样", {"GMV"}, "口语化 + 昨天"),
    ("U08", "各地区卖得怎么样", {"GMV"}, "口语化 + 缺时间"),
    ("U09", "上个月订单量多少", {"order_count"}, "明确指标 - 订单数"),
    ("U10", "东北的成交额", {"GMV"}, "地域 + 缺时间"),
    ("U11", "近一周卖了多少", {"GMV"}, "近N天"),
    ("U12", "我们的销售额数据", {"GMV"}, "口语 + 无时间"),
    ("U13", "本季度GMV是多少", {"GMV"}, "季度时间"),
    ("U14", "这个月日活", {"DAU"}, "明确DAU + 本月"),
    ("U15", "每个区域的订单情况", {"order_count"}, "分组 + 缺时间"),
]


def analyze_case(case_id, question, expected_terms):
    cands = generate_candidates(
        question,
        keywords=[],
        today=date(2026, 8, 12),
        max_candidates=5,
    )
    result = {
        "case_id": case_id,
        "question": question,
        "note": "",
        "n_candidates": len(cands),
        "covered_expected_terms": [],
        "missing_expected_terms": [],
        "candidates": [],
        "has_structure_issue": False,
        "issues": [],
    }
    # 收集所有候选涉及的指标
    covered = set()
    for c in cands:
        for m in c.get("measures", []):
            covered.add(m.get("business_term", ""))
    result["covered_expected_terms"] = sorted(covered & expected_terms)
    result["missing_expected_terms"] = sorted(expected_terms - covered)

    for c in cands:
        sql = c["sql_preview"]
        explain = c.get("_explain", "")
        # 结构检查:引用的别名必须都能在 JOIN 中找到
        aliases = set(re.findall(r"\b([a-z])\.[a-zA-Z_]", sql))
        join_aliases = set(re.findall(r"JOIN dw\.[\w.]+ AS ([a-z])", sql))
        dangling = aliases - join_aliases - {"t"}
        cand_entry = {
            "candidate_id": c.get("_candidate_id"),
            "confidence": c.get("confidence"),
            "score": c.get("_score"),
            "explain": explain,
            "sql": sql,
            "dangling_aliases": sorted(dangling),
        }
        if dangling:
            result["has_structure_issue"] = True
            result["issues"].append(f"{c.get('_candidate_id')}: 悬空别名 {sorted(dangling)}")
        result["candidates"].append(cand_entry)

    # 无候选 -> 严重问题
    if not cands:
        result["has_structure_issue"] = True
        result["issues"].append("无候选生成")

    return result


def verify_on_doris(sql, conn):
    """在 Doris 实际执行,返回 (ok, rows_or_error)。"""
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        return True, len(rows)
    except Exception as e:
        return False, str(e)[:120]


def run(verify_doris=False):
    conn = None
    if verify_doris:
        import pymysql
        conn = pymysql.connect(
            host=os.getenv("DORIS_HOST", "192.168.137.52"), port=9030,
            user=os.getenv("DORIS_USER", "root"),
            password=os.getenv("DORIS_PASSWORD", ""),
            database="dw", charset="utf8mb4",
        )

    print("=" * 80)
    print("模拟用户猜想测试报告")
    print(f"日期: {date.today()}  | 候选上限: 5/题  | 时间基准: 2026-08-12")
    print("=" * 80)

    total_cases = len(USER_GUESS_CASES)
    total_cands = 0
    cases_with_issue = 0
    sql_exec_counts = {"ok": 0, "fail": 0, "n/a": 0}
    n_sql_total = 0

    for case_id, question, expected_terms, note in USER_GUESS_CASES:
        r = analyze_case(case_id, question, expected_terms)
        r["note"] = note
        total_cands += r["n_candidates"]

        print(f"\n{'─' * 80}")
        print(f"[{r['case_id']}] 「{r['question']}」  ({r['note']})")
        print(f"  候选数: {r['n_candidates']}  | 期望指标命中: "
              f"{r['covered_expected_terms'] or '无'} "
              f"{'| 缺失: ' + str(r['missing_expected_terms']) if r['missing_expected_terms'] else ''}")

        # 只展示前 3 个候选 + SQL 执行结果
        for c in r["candidates"][:3]:
            sql_short = " ".join(c["sql"].split())[:90]
            print(f"  · [{c['candidate_id']}] conf={c['confidence']} "
                  f"score={c['score']}")
            print(f"      {c['explain'][:70]}")
            print(f"      SQL: {sql_short}...")
            # Doris 执行验证
            if conn is not None and c["sql"]:
                n_sql_total += 1
                ok, detail = verify_on_doris(c["sql"], conn)
                if ok:
                    sql_exec_counts["ok"] += 1
                    print(f"      → ✅ Doris 执行成功 ({detail} 行)")
                else:
                    sql_exec_counts["fail"] += 1
                    print(f"      → ❌ Doris 执行失败: {detail}")
            elif conn is not None:
                sql_exec_counts["n/a"] += 1

        if r["issues"]:
            cases_with_issue += 1
            print(f"  ⚠️ 问题: {r['issues']}")

    # 汇总
    print(f"\n{'=' * 80}")
    print("汇总")
    print(f"  用例数: {total_cases}")
    print(f"  生成候选总数: {total_cands} (平均 {total_cands / total_cases:.1f}/题)")
    print(f"  有结构问题的用例: {cases_with_issue}")
    if conn is not None:
        print(f"  Doris 执行: 成功 {sql_exec_counts['ok']} / 失败 {sql_exec_counts['fail']} "
              f"/ 跳过 {sql_exec_counts['n/a']} (共验证 {n_sql_total})")
        exec_rate = sql_exec_counts["ok"] / n_sql_total if n_sql_total else 0
        print(f"  SQL 可执行率: {exec_rate:.1%}")
    print("=" * 80)

    if conn:
        conn.close()
    return cases_with_issue == 0


if __name__ == "__main__":
    import os
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    verify = "--verify-doris" in sys.argv
    ok = run(verify_doris=verify)
    sys.exit(0 if ok else 1)
