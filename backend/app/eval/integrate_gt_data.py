"""LiveSQLBench 官方 GT 数据合入脚本(适配本仓库).

官方 `integrate_gt_data.py` 的适配版,针对我们已下载的数据结构:
    <data_root>/livesqlbench_data.jsonl   # 公开版(已下载)

用法(收到官方 GT 邮件后):
    python app/eval/integrate_gt_data.py \
        --gt-file <path/to/gt.jsonl> \
        --data-root D:/datasets/livesqlbench-base-lite-sqlite

逻辑(对齐官方):
  1. 按 instance_id 匹配 GT 与公开数据
  2. 合入 sol_sql / test_cases / external_knowledge 三个字段
  3. 写回 livesqlbench_data.jsonl(先备份 .bak)
  4. 输出合并统计

额外适配:
  - 兼容 GT 中 sol_sql 为 str 或 list(统一规范为 list)
  - 输出每库/类别的 GT 覆盖率,便于核对
"""
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("integrate_gt")

# GT 中需要合入的受保护字段(与官方一致)
PROTECTED_FIELDS = ["sol_sql", "test_cases", "external_knowledge"]


def load_jsonl(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_jsonl(data: list[dict], path: Path) -> None:
    backup = path.with_suffix(".jsonl.bak")
    if path.exists():
        path.rename(backup)
        logger.info(f"已备份原文件 -> {backup}")
    with open(path, "w", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    logger.info(f"已写入 {path}")


def normalize_sol_sql(sol_sql: Any) -> list[str]:
    """把 sol_sql 规范为 list[str].

    官方 GT 中 sol_sql 可能为:
      - list[str]  : ['SELECT ...']      (最常见)
      - str        : 'SELECT ...'
      - list[dict] : [{'sql': '...', ...}]
    """
    if sol_sql is None:
        return []
    if isinstance(sol_sql, str):
        return [sol_sql] if sol_sql.strip() else []
    if isinstance(sol_sql, list):
        out = []
        for item in sol_sql:
            if isinstance(item, str):
                if item.strip():
                    out.append(item)
            elif isinstance(item, dict):
                sql = item.get("sql") or item.get("pred_sql") or ""
                if sql:
                    out.append(sql)
        return out
    return []


def integrate(public: list[dict], gt: list[dict]) -> tuple[list[dict], dict]:
    """按 instance_id 合入 GT 字段.返回 (整合数据, 统计)."""
    gt_lookup = {item["instance_id"]: item for item in gt}
    matched = 0
    missing = 0
    sol_filled = 0
    tc_filled = 0
    ek_filled = 0

    out = []
    for item in public:
        iid = item.get("instance_id", "")
        new_item = dict(item)
        if iid in gt_lookup:
            gt_item = gt_lookup[iid]
            matched += 1
            for field in PROTECTED_FIELDS:
                if field in gt_item:
                    new_item[field] = gt_item[field]
                    if field == "sol_sql":
                        new_item["sol_sql"] = normalize_sol_sql(gt_item[field])
                        sol_filled += 1 if new_item["sol_sql"] else 0
                    elif field == "test_cases" and gt_item[field]:
                        tc_filled += 1
                    elif field == "external_knowledge" and gt_item[field]:
                        ek_filled += 1
        else:
            missing += 1
            if missing <= 5:
                logger.warning(f"GT 中找不到 {iid}")
        out.append(new_item)

    stats = {
        "public_total": len(public),
        "gt_total": len(gt),
        "matched": matched,
        "missing": missing,
        "sol_filled": sol_filled,
        "test_cases_filled": tc_filled,
        "external_knowledge_filled": ek_filled,
    }
    return out, stats


def main():
    parser = argparse.ArgumentParser(description="合入 LiveSQLBench GT 数据")
    parser.add_argument("--gt-file", required=True, help="官方 GT jsonl 路径")
    parser.add_argument("--data-root", required=True,
                        help="数据根目录(含 livesqlbench_data.jsonl)")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    public_file = data_root / "livesqlbench_data.jsonl"
    gt_file = Path(args.gt_file)

    if not gt_file.exists():
        raise FileNotFoundError(f"GT 文件不存在: {gt_file}")
    if not public_file.exists():
        raise FileNotFoundError(f"公开数据不存在: {public_file}")

    public = load_jsonl(public_file)
    gt = load_jsonl(gt_file)
    logger.info(f"公开数据 {len(public)} 条, GT {len(gt)} 条")

    integrated, stats = integrate(public, gt)

    # 检查 GT 是否已包含 sol_sql(避免重复合入空数据)
    if stats["sol_filled"] == 0:
        logger.warning(
            "未合入任何 sol_sql。请确认 GT 文件格式正确(应含 instance_id + sol_sql)"
        )

    save_jsonl(integrated, public_file)

    print("\n" + "=" * 55)
    print("  GT 合并结果")
    print("=" * 55)
    for k, v in stats.items():
        print(f"  {k:24s} {v}")
    print("=" * 55)


if __name__ == "__main__":
    main()
