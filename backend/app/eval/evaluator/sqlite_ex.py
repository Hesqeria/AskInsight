"""SQLite 执行后端（LiveSQLBench 接入核心）。

让评测框架在不依赖 Docker/PostgreSQL 的情况下执行 LiveSQLBench
题目的候选 SQL。用 SQLite（Python 标准库）加载数据集自带的 .db
文件,执行 pred_sql 与 sol_sql 并比较结果集。

打分逻辑对齐官方 LiveSQLBench Soft EX:
  1. 去注释 / DISTINCT / 空 SELECT 噪音
  2. 执行 pred 与 sol
  3. 结果预处理（数值 round 2dp、NULL 统一、类型规范化）
  4. 有序或无序比较（依据 conditions）

复用 app.eval.utils.result_sets_equal 作为核心比较器,并保留
官方对 DATE 归一化的近似处理。
"""
from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.core.log import logger


# --------------------------------------------------------------------------- #
# SQLite 数学函数注册(对齐官方 SQLite 评测环境)
# --------------------------------------------------------------------------- #
# LiveSQLBench 官方 SQLite 环境加载了 math 扩展,提供 POWER/LOG/LN/SQRT 等
# PostgreSQL 风格函数。Python 内置 sqlite3 不带这些,需手动注册。
# 参考: SQLite math functions (https://sqlite.org/lang_mathfunc.html)
_MATH_FUNCTIONS = {
    "POWER": lambda base, exp: math.pow(base, exp),
    "POW": lambda base, exp: math.pow(base, exp),
    "SQRT": lambda x: math.sqrt(x),
    "LN": lambda x: math.log(x),
    "EXP": lambda x: math.exp(x),
    "FLOOR": lambda x: math.floor(x),
    "CEIL": lambda x: math.ceil(x),
    "CEILING": lambda x: math.ceil(x),
    "SIN": lambda x: math.sin(x),
    "COS": lambda x: math.cos(x),
    "TAN": lambda x: math.tan(x),
    "ASIN": lambda x: math.asin(x),
    "ACOS": lambda x: math.acos(x),
    "ATAN": lambda x: math.atan(x),
    "ATAN2": lambda y, x: math.atan2(y, x),
    "SIGN": lambda x: 1 if x > 0 else (-1 if x < 0 else 0),
    "TRUNC": lambda x: math.trunc(x),
    "MOD": lambda x, y: x % y,
    "PI": lambda: math.pi,
}


def register_math_functions(conn: sqlite3.Connection) -> None:
    """给 SQLite 连接注册数学函数(POWER/LOG/SQRT 等).

    SQLite 的 LOG 支持两种签名(对齐官方 math 扩展):
      - LOG(X)      : 自然对数
      - LOG(B, X)   : 以 B 为底的对数
    分别用不同的函数名注册,避免参数数量冲突。
    """
    for name, fn in _MATH_FUNCTIONS.items():
        if name == "LOG":
            continue  # LOG 特殊处理(支持 1 参/2 参)
        try:
            conn.create_function(name, fn.__code__.co_argcount, fn)
        except Exception as e:
            logger.debug(f"注册函数 {name} 失败: {e}")
    try:
        conn.create_function("LOG", 1, math.log)               # LOG(X)
        conn.create_function("LOG", 2, lambda b, x: math.log(x) / math.log(b))  # LOG(B,X)
        conn.create_function("LOG10", 1, math.log10)           # 兼容单参 LOG10
    except Exception as e:
        logger.debug(f"注册 LOG 函数失败: {e}")


# --------------------------------------------------------------------------- #
# SQL 预处理（对齐官方 test_utils.py 的 remove_* 系列）
# --------------------------------------------------------------------------- #
def remove_comments(sql) -> str:
    """去掉 SQL 中的行/块注释,保留字符串字面量内容.

    兼容 list 输入(官方 test_case 会传入 SQL 列表),逐条处理.
    """
    if sql is None:
        return sql
    if isinstance(sql, list):
        return [remove_comments(s) for s in sql]
    if not isinstance(sql, str):
        return sql
    # 先保护字符串字面量
    parts = re.split(r"('(?:''|[^'])*')", sql, flags=re.DOTALL)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # 字符串字面量
            out.append(part)
        else:
            # 去块注释
            part = re.sub(r"/\*.*?\*/", "", part, flags=re.DOTALL)
            # 去行注释
            part = re.sub(r"--[^\n]*", "", part)
            out.append(part)
    return "".join(out)


def remove_distinct(sql) -> str:
    """把 SELECT DISTINCT 降级为 SELECT(Soft EX 对齐).

    兼容 list 输入.
    """
    if sql is None:
        return sql
    if isinstance(sql, list):
        return [remove_distinct(s) for s in sql]
    if not isinstance(sql, str):
        return sql
    return re.sub(r"(?i)\bselect\s+distinct\b", "SELECT", sql)


def remove_order_by(sql: str) -> str:
    """去掉外层 ORDER BY(无序比较时用)."""
    if not sql:
        return sql
    # 只去最外层的 ORDER BY(不含子查询内)
    # 简化:找到顶层 ORDER BY(不在括号内)
    depth = 0
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and sql[i:i+8].upper() == "ORDER BY":
            # 去掉 ORDER BY 及其后内容(到 LIMIT 之前)
            rest = sql[i+9:]
            limit_m = re.search(r"(?i)\blimit\b", rest)
            if limit_m:
                return sql[:i] + " LIMIT" + rest[limit_m.start()+6:]
            return sql[:i].rstrip()
        i += 1
    return sql


def preprocess_sql_for_compare(sql: str, *, ordered: bool = True) -> str:
    """完整预处理一条 SQL 用于比较."""
    if not sql:
        return sql
    sql = remove_comments(sql).strip()
    if not sql.rstrip().endswith(";"):
        sql += ";"
    sql = remove_distinct(sql)
    if not ordered:
        sql = remove_order_by(sql)
    return sql


# --------------------------------------------------------------------------- #
# 值规范化（对齐官方 preprocess_results）
# --------------------------------------------------------------------------- #
def normalize_value(v) -> object:
    """规范化单个值用于比较:
    - None/'' 统一为 None
    - 数值 round 2 位（对齐官方,避免浮点差异）
    - 其余转 str
    """
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v), 2)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return str(v)


def normalize_rows(rows: list[tuple]) -> list[tuple]:
    """规范化整个结果集."""
    return [tuple(normalize_value(v) for v in row) for row in rows]


# --------------------------------------------------------------------------- #
# SQLite 执行
# --------------------------------------------------------------------------- #
@dataclass
class SqliteExecResult:
    ok: bool = True
    error: str = ""
    rows: list[tuple] = field(default_factory=list)


class SqliteBackend:
    """按数据库文件路径连接 SQLite,支持执行多条 SQL(会话内)."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        """打开连接(Read-Only 模式,防止评测污染源数据)."""
        uri = f"file:{self.db_path}?mode=ro"
        try:
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # 注册数学函数(对齐官方 SQLite 评测环境)
            register_math_functions(self._conn)
        except sqlite3.Error as e:
            raise RuntimeError(f"无法打开 SQLite 库 {self.db_path}: {e}")

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    def execute(self, sql: str, max_rows: int = 10000) -> SqliteExecResult:
        """执行单条 SQL,返回结果."""
        if self._conn is None:
            return SqliteExecResult(ok=False, error="connection not open")
        try:
            cur = self._conn.execute(sql)
            # 区分 SELECT(DML 返回行)与 DDL/DML
            stripped = sql.lstrip().upper()
            if stripped.startswith("SELECT") or stripped.startswith("WITH") or stripped.startswith("PRAGMA"):
                rows = cur.fetchall()
                return SqliteExecResult(ok=True, rows=[tuple(r) for r in rows[:max_rows]])
            return SqliteExecResult(ok=True, rows=[])
        except sqlite3.Error as e:
            return SqliteExecResult(ok=False, error=str(e)[:300])

    def list_tables(self) -> list[str]:
        """列出所有表名(用于 schema 上下文)."""
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.Error:
            return []

    def get_schema(self, table: str) -> str:
        """返回单表建表语句(作为 DDL 上下文)."""
        if self._conn is None:
            return ""
        try:
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            return row[0] if row else ""
        except sqlite3.Error:
            return ""

    def full_schema(self) -> str:
        """返回全部表的 DDL(拼接)."""
        parts = []
        for t in self.list_tables():
            ddl = self.get_schema(t)
            if ddl:
                parts.append(ddl)
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 单题评测（LiveSQLBench Soft EX）
# --------------------------------------------------------------------------- #
@dataclass
class SqliteVerdict:
    """一题在 SQLite 上的评测结果."""
    instance_id: str
    question: str
    category: str          # "Query" | "Management"
    difficulty: str
    pred_sql: str
    sol_sql: str
    ex_correct: bool
    pred_rows: int = 0
    sol_rows: int = 0
    error: str = ""
    match_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "question": self.question,
            "category": self.category,
            "difficulty": self.difficulty,
            "ex_correct": self.ex_correct,
            "pred_rows": self.pred_rows,
            "sol_rows": self.sol_rows,
            "error": self.error,
            "match_reason": self.match_reason,
            "pred_sql": self.pred_sql,
            "sol_sql": self.sol_sql,
        }


def evaluate_sqlite(
    backend: SqliteBackend,
    *,
    instance_id: str,
    question: str,
    pred_sql: str,
    sol_sql: str,
    category: str = "Query",
    difficulty: str = "",
    ordered: bool = True,
    float_rtol: float = 1e-6,
) -> SqliteVerdict:
    """执行并比较 pred 与 sol(Soft EX).

    ordered: 是否要求结果有序(Management/带 ORDER BY 的 Query 用 True)。
    官方对 "order in conditions" 用有序比较,否则无序。
    """
    if not pred_sql or not pred_sql.strip():
        return SqliteVerdict(
            instance_id=instance_id, question=question, category=category,
            difficulty=difficulty, pred_sql=pred_sql or "", sol_sql=sol_sql,
            ex_correct=False, error="pred_sql 为空",
        )

    # 1. 预处理
    sol_cmp = preprocess_sql_for_compare(sol_sql, ordered=ordered)
    pred_cmp = preprocess_sql_for_compare(pred_sql, ordered=ordered)

    # 2. 执行
    sol_res = backend.execute(sol_cmp)
    if not sol_res.ok:
        return SqliteVerdict(
            instance_id=instance_id, question=question, category=category,
            difficulty=difficulty, pred_sql=pred_sql, sol_sql=sol_sql,
            ex_correct=False, error=f"sol_sql 执行失败: {sol_res.error}",
        )
    pred_res = backend.execute(pred_cmp)
    if not pred_res.ok:
        return SqliteVerdict(
            instance_id=instance_id, question=question, category=category,
            difficulty=difficulty, pred_sql=pred_sql, sol_sql=sol_sql,
            ex_correct=False, error=f"pred_sql 执行失败: {pred_res.error}",
        )

    # 3. 规范化 + 比较
    from app.eval.utils import result_sets_equal
    gold_rows = normalize_rows(sol_res.rows)
    pred_rows = normalize_rows(pred_res.rows)
    correct = result_sets_equal(gold_rows, pred_rows, order_matters=ordered)

    return SqliteVerdict(
        instance_id=instance_id, question=question, category=category,
        difficulty=difficulty, pred_sql=pred_sql, sol_sql=sol_sql,
        ex_correct=correct, pred_rows=len(pred_rows), sol_rows=len(gold_rows),
        match_reason="exact" if correct else "mismatch",
    )
