"""Monthly Few-Shot evaluation & pruning (OPT-M2).

Usage:
    python -m app.scripts.evaluate_few_shot            # dry-run: report only
    python -m app.scripts.evaluate_few_shot --apply    # apply pruning

Logic (per 数仓问数实际应用-PRD OPT-M2):
  - hit_rate = hit_count / (days_since_created_at + 1)  (approximate daily hits)
  - Examples with hit_rate below threshold AND age > min_age are flagged for
    archive (prune).
  - Low-satisfaction examples (satisfaction < 3) are flagged regardless.
  - Outputs a Markdown report to app/eval/runs/.
"""
import argparse
import io
import sys
from datetime import datetime

import pymysql

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Tuning knobs.
HIT_RATE_THRESHOLD = 0.05   # <5% daily hit rate -> candidate for pruning
MIN_AGE_DAYS = 30           # only evaluate examples older than this
LOW_SATISFACTION = 3        # satisfaction < this -> flag regardless
TOP_KEEP = 50               # keep at most top-50 high-hit examples per domain


def _conn():
    import os as _os
    return pymysql.connect(
        host=_os.getenv("DORIS_HOST", "192.168.137.52"), port=9030,
        user=_os.getenv("DORIS_USER", "root"),
        password=_os.getenv("DORIS_PASSWORD", ""), database="data_agent",
        charset="utf8mb4",
    )


def evaluate(dry_run: bool = True) -> str:
    c = _conn()
    cur = c.cursor()
    now = datetime.now()

    cur.execute(
        """SELECT example_id, question, sql_text, entity, complexity, source,
                  satisfaction, business_domain, hit_count, created_at, last_used_at
           FROM few_shot_examples ORDER BY created_at"""
    )
    rows = cur.fetchall()

    total = len(rows)
    flags = []          # (example_id, reason, detail)
    by_domain = {}

    for r in rows:
        (eid, question, sql, entity, complexity, source, sat,
         domain, hits, created_at, last_used_at) = r
        age_days = max((now - created_at).days, 1) if created_at else 1
        hit_rate = hits / age_days
        by_domain.setdefault(domain or "other", []).append((hit_rate, eid))

        # Rule 1: low satisfaction.
        if sat is not None and sat < LOW_SATISFACTION:
            flags.append((eid, f"satisfaction={sat}", question[:40]))

        # Rule 2: old + low hit rate.
        if age_days >= MIN_AGE_DAYS and hit_rate < HIT_RATE_THRESHOLD:
            flags.append(
                (eid, f"hit_rate={hit_rate:.3f} age={age_days}d", question[:40]))

    # Rule 3: keep top-50 per domain (implicit in reports; pruning only flags).
    report = [
        "# Few-Shot 月评估报告",
        f"\n> 生成时间: {now:%Y-%m-%d %H:%M:%S} ｜ 模式: {'DRY-RUN（仅报告）' if dry_run else 'APPLY（执行清理）'}",
        f"> 总示例: {total} 条 ｜ 命中监控: hit_count 由 feedback_recall 自动累加\n",
        "## 一、总体统计",
        "| 业务域 | 示例数 | 平均命中率 |",
        "| --- | --- | --- |",
    ]
    for dom in sorted(by_domain):
        n = len(by_domain[dom])
        avg_rate = sum(r for r, _ in by_domain[dom]) / n
        report.append(f"| {dom} | {n} | {avg_rate:.4f} |")

    report.append("\n## 二、待清理候选")
    if not flags:
        report.append("\n无候选（所有示例命中率达标）")
    else:
        report.append("| example_id | 原因 | 问题 |")
        report.append("| --- | --- | --- |")
        for eid, reason, q in flags:
            report.append(f"| {eid[:24]} | {reason} | {q} |")

    report.append("\n## 三、操作")
    if dry_run:
        report.append(f"\n建议清理 {len(flags)} 条。运行 `--apply` 实际执行：\n")
        report.append("```sql")
        for eid, _, _ in flags:
            report.append(f"-- DELETE FROM few_shot_examples WHERE example_id = '{eid}';")
        report.append("```")
    else:
        pruned = 0
        for eid, _, _ in flags:
            try:
                cur.execute("DELETE FROM few_shot_examples WHERE example_id=%s", (eid,))
                pruned += 1
            except Exception as e:
                report.append(f"- 删除失败 {eid}: {e}")
        c.commit()
        report.append(f"\n已清理 {pruned} 条低效示例。")

    # Persist report.
    import os
    runs_dir = os.path.join(os.path.dirname(__file__), "..", "eval", "runs")
    os.makedirs(runs_dir, exist_ok=True)
    fname = os.path.join(runs_dir, f"fewshot_monthly_{now:%Y%m%d}.md")
    with open(fname, "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    c.close()
    return "\n".join(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="执行清理（默认 dry-run）")
    args = parser.parse_args()
    print(evaluate(dry_run=not args.apply))
