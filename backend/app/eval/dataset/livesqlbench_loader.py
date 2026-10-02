"""LiveSQLBench 数据加载器。

从本地 livesqlbench-base-lite-sqlite 数据集目录加载题目、定位 SQLite
数据库文件、组装评测所需的上下文(DDL + 列含义 + HKB 外部知识)。

数据集目录结构(下载自 HuggingFace 后本地解压):
    <root>/
      livesqlbench_data.jsonl          # 题目(含 sol_sql 的完整版, 邮件获取)
      databases/
        <db_name>.db                   # SQLite 数据库文件
        <db_name>/
          column_meaning.json          # 列含义(可选)
          hkb/                         # 分层知识库(可选)
          ...
      schema/ 或 *.json                # schema 元数据(可选)

题目 jsonl 字段(官方):
    instance_id, selected_database, query, category, difficulty_tier,
    sol_sql(完整版), test_cases(完整版), external_knowledge, ...
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class LiveSQLBenchTask:
    """一条 LiveSQLBench 评测任务."""
    instance_id: str
    database: str
    query: str
    category: str = "Query"          # Query | Management
    difficulty: str = ""
    sol_sql: str = ""                # 完整版才有
    external_knowledge: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "database": self.database,
            "query": self.query,
            "category": self.category,
            "difficulty": self.difficulty,
            "sol_sql": self.sol_sql,
            "external_knowledge": self.external_knowledge,
        }


class LiveSQLBenchLoader:
    """从本地目录加载 LiveSQLBench 数据集."""

    def __init__(self, data_root: str | Path):
        self.root = Path(data_root)
        self._jsonl_path = self.root / "livesqlbench_data.jsonl"
        if not self._jsonl_path.exists():
            # 兼容备用路径
            candidates = list(self.root.rglob("*.jsonl"))
            if candidates:
                self._jsonl_path = candidates[0]
        self._db_cache: dict[str, Path] = {}

    # ------------------------------------------------------------------ #
    # 题目加载
    # ------------------------------------------------------------------ #
    def load_tasks(self, category: Optional[str] = None,
                   limit: Optional[int] = None,
                   offset: int = 0) -> list[LiveSQLBenchTask]:
        """加载题目列表,可按类别过滤."""
        if not self._jsonl_path.exists():
            raise FileNotFoundError(
                f"找不到 {self._jsonl_path}。请先下载数据集并放到 {self.root}"
            )
        tasks = []
        with open(self._jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = self._parse_task(d)
                if category and t.category != category:
                    continue
                tasks.append(t)
        if offset:
            tasks = tasks[offset:]
        if limit is not None:
            tasks = tasks[:limit]
        return tasks

    def _parse_task(self, d: dict) -> LiveSQLBenchTask:
        sol = d.get("sol_sql", "")
        if isinstance(sol, list):
            # GT 合入后 sol_sql 是 list[str];取第一条非空(或全部)
            sol_str = next((s for s in sol if s and str(s).strip()), "")
        else:
            sol_str = str(sol or "")
        return LiveSQLBenchTask(
            instance_id=str(d.get("instance_id", "")),
            database=str(d.get("selected_database", "")),
            query=str(d.get("query", "")),
            category=str(d.get("category", "Query")),
            difficulty=str(d.get("difficulty_tier", "")),
            sol_sql=sol_str,
            external_knowledge=d.get("external_knowledge", []) or [],
            raw=d,
        )

    # ------------------------------------------------------------------ #
    # 数据库定位
    # ------------------------------------------------------------------ #
    def find_db_file(self, db_name: str) -> Optional[Path]:
        """定位指定数据库的 SQLite 文件.

        支持多种命名(适应 hf-mirror 下载的实际结构):
          - databases/<db>/<db>_template.sqlite  (LiveSQLBench 官方命名)
          - databases/<db>.db
          - databases/<db>/<db>.db
        """
        if db_name in self._db_cache:
            return self._db_cache[db_name]
        if not db_name:
            return None

        # 常见命名
        candidates = [
            self.root / "databases" / db_name / f"{db_name}_template.sqlite",
            self.root / "databases" / f"{db_name}.db",
            self.root / "databases" / db_name / f"{db_name}.db",
            self.root / "databases" / db_name / "database.db",
            self.root / f"{db_name}.db",
        ]
        # 额外递归查找(按文件基名)
        if not any(c.exists() for c in candidates):
            hits = list(self.root.rglob(f"*{db_name}*.sqlite"))
            if hits:
                candidates = hits[:1]

        for c in candidates:
            if c.exists():
                self._db_cache[db_name] = c
                return c
        return None

    # ------------------------------------------------------------------ #
    # 上下文构建（DDL + 列含义 + HKB）
    # ------------------------------------------------------------------ #
    def build_context(self, db_name: str) -> dict:
        """组装一个数据库的完整评测上下文.

        Returns:
            {
              "db_file": str,           # SQLite 文件路径
              "ddl": str,               # 全部表建表语句
              "column_meanings": str,   # 列含义文本
              "hkb": str,               # 分层知识库文本
              "tables": [str],          # 表名列表
            }
        """
        db_file = self.find_db_file(db_name)
        ddl = ""
        tables: list[str] = []
        if db_file:
            # 用 SqliteBackend 提取 DDL
            try:
                from app.eval.evaluator.sqlite_ex import SqliteBackend
                with SqliteBackend(db_file) as backend:
                    tables = backend.list_tables()
                    ddl = backend.full_schema()
            except Exception:
                pass

        column_meanings = self._load_column_meanings(db_name)
        hkb = self._load_hkb(db_name)

        return {
            "db_file": str(db_file) if db_file else "",
            "ddl": ddl,
            "column_meanings": column_meanings,
            "hkb": hkb,
            "tables": tables,
        }

    def _load_column_meanings(self, db_name: str) -> str:
        """加载列含义文件(如果有)."""
        db_dir = self.root / "databases" / db_name
        for fname in ("column_meaning.json", "column_meaning.jsonl",
                      f"{db_name}_column_meaning.json"):
            p = db_dir / fname
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    return self._render_column_meanings(data)
                except Exception:
                    pass
        return ""

    def _render_column_meanings(self, data) -> str:
        """把列含义 JSON 渲染成文本.

        支持两种格式:
          - { "db|Table|Column": "meaning" }   (LiveSQLBench 实际格式)
          - { "Table": { "Column": "meaning" } }
          - [{table, column, meaning}]
        """
        parts = []
        if isinstance(data, dict):
            for key, meaning in data.items():
                if isinstance(meaning, dict):
                    # { Table: { Column: meaning } }
                    for col, m in meaning.items():
                        parts.append(f"{key}.{col}: {m}")
                else:
                    # { db|Table|Column: meaning } 或 { col: meaning }
                    if "|" in str(key):
                        db, table, col = str(key).split("|", 2)
                        parts.append(f"{table}.{col}: {meaning}")
                    else:
                        parts.append(f"{key}: {meaning}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    t = item.get("table", "")
                    c = item.get("column", "")
                    m = item.get("meaning", item.get("description", ""))
                    if t and c:
                        parts.append(f"{t}.{c}: {m}")
        return "\n".join(parts)

    def _load_hkb(self, db_name: str) -> str:
        """加载分层知识库(HKB),可能为 JSON 或文本."""
        db_dir = self.root / "databases" / db_name
        hkb_dir = db_dir / "hkb"
        parts = []
        if hkb_dir.exists():
            for p in sorted(hkb_dir.iterdir()):
                if p.suffix in (".json", ".jsonl"):
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        parts.append(self._render_hkb(data))
                    except Exception:
                        parts.append(p.read_text(encoding="utf-8", errors="ignore"))
                elif p.suffix in (".txt", ".md", ".doc"):
                    parts.append(p.read_text(encoding="utf-8", errors="ignore"))
        return "\n\n".join(parts)

    def _render_hkb(self, data) -> str:
        """渲染 HKB(可能是 dict / list / str)."""
        if isinstance(data, str):
            return data
        if isinstance(data, list):
            out = []
            for item in data:
                if isinstance(item, dict):
                    # 常见字段: id/name/definition/description/related_to
                    label = item.get("name") or item.get("id") or item.get("term")
                    desc = item.get("definition") or item.get("description") or item.get("meaning")
                    if label and desc:
                        out.append(f"{label}: {desc}")
                    elif desc:
                        out.append(str(desc))
                else:
                    out.append(str(item))
            return "\n".join(out)
        if isinstance(data, dict):
            out = []
            for k, v in data.items():
                out.append(f"{k}: {v}")
            return "\n".join(out)
        return str(data)
