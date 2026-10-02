"""LiveSQLBench-Base-Lite-SQLite 数据下载脚本。

用 hf-mirror.com 镜像拉取数据集(本机 huggingface.co 直连超时)。
下载到本地目录后,可直接供 app.eval.run_livesqlbench 使用。

用法:
    python app/eval/download_livesqlbench.py --out D:/datasets/livesqlbench-base-lite-sqlite

数据结构(18 个库,每库 4 文件 + 主 jsonl):
    <db>_template.sqlite          # SQLite 数据库
    <db>_schema.txt               # DDL
    <db>_column_meaning_base.json # 列含义
    <db>_kb.jsonl                 # 分层知识库(HKB)
    livesqlbench_data_sqlite.jsonl # 题目
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://hf-mirror.com/datasets/birdsql/livesqlbench-base-lite-sqlite/resolve/main/"

# 每个库的文件(由 API 探测补齐,这里先列出已知 18 库)
DBS = [
    "alien", "archeology", "credit", "cross_db", "crypto", "cybermarket",
    "disaster", "fake", "gaming", "insider", "mental", "museum",
    "news", "polar", "robot", "solar", "vaccine", "virtual",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; data-download)"}


def download(url: str, dest: Path, chunk_size: int = 1 << 20) -> bool:
    """下载单个文件,返回是否成功."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [跳过] {dest.name} 已存在 ({dest.stat().st_size//1024} KB)")
        return True
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            total = 0
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
        print(f"  [OK] {dest.name} ({total//1024} KB)")
        return True
    except Exception as e:
        print(f"  [FAIL] {dest.name}: {str(e)[:80]}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--skip-db", action="store_true",
                        help="跳过 .sqlite 库文件(只下元数据)")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    ok = 0
    fail = 0

    # 1. 主题目文件
    print("== 下载题目 ==")
    if download(BASE_URL + "livesqlbench_data_sqlite.jsonl",
                out / "livesqlbench_data.jsonl"):
        ok += 1
    else:
        fail += 1

    # 2. 每个库的文件(URL 需带 <db>/ 目录前缀,见 API siblings)
    for db in DBS:
        print(f"== {db} ==")
        files = [
            (f"{db}/{db}_template.sqlite", out / "databases" / db / f"{db}_template.sqlite"),
            (f"{db}/{db}_schema.txt", out / "databases" / db / f"{db}_schema.txt"),
            (f"{db}/{db}_column_meaning_base.json",
             out / "databases" / db / f"{db}_column_meaning.json"),
            (f"{db}/{db}_kb.jsonl", out / "databases" / db / "hkb" / f"{db}_kb.jsonl"),
        ]
        for fname, dest in files:
            if args.skip_db and fname.endswith(".sqlite"):
                continue
            if download(BASE_URL + fname, dest):
                ok += 1
            else:
                fail += 1

    print("\n" + "=" * 50)
    print(f"完成: {ok} 成功, {fail} 失败")
    print(f"输出目录: {out}")
    if fail:
        print("有文件下载失败,可重跑本脚本(已下载的会跳过)")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
