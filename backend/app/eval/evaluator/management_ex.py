"""Management(CRUD)类 LiveSQLBench 评测器。

Management 题的 test_cases 是**可执行 Python 断言代码**,依赖官方评测环境
的 `execute_queries(sqls, db_name, conn)` 约定,返回
    (result, error, timeout)
其中 result 是嵌套列表: result[i] = 第 i 条 SQL 的结果(rows)。

与 Query 类不同:
  - Management 题会修改数据库(ALTER/CREATE/UPDATE/DELETE/触发器)
  - 因此评测用**可写连接**,且每题用**独立数据库副本**避免相互污染

设计:
  1. 从 template.sqlite 复制出每题独立的临时库
  2. 在临时库上执行 pred_sqls(可写)
  3. exec test_case 代码,提供沙箱(execute_queries / date / conn)
  4. 断言不抛异常 = 通过;抛 AssertionError/其他 = 失败
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.core.log import logger
from app.eval.evaluator.sqlite_ex import (
    register_math_functions,
    remove_comments, remove_distinct, remove_order_by,
    preprocess_sql_for_compare, normalize_rows,
)


# --------------------------------------------------------------------------- #
# 官方 test_case 依赖的工具函数(注入沙箱,对齐官方环境)
# --------------------------------------------------------------------------- #
def _make_helpers(conn: sqlite3.Connection):
    """构造官方 test_case 可能引用的辅助函数(remove_distinct 等)."""
    from app.eval.evaluator.management_ex import _make_execute_queries
    execute_queries = _make_execute_queries(conn)

    def ex_base(pred_sqls, sol_sqls, db_name=None, conn=None, conditions=None):
        """官方 ex_base:比较 pred 与 sol 结果集,返回 1/0."""
        if not pred_sqls or not sol_sqls:
            return 0
        if isinstance(pred_sqls, str):
            pred_sqls = [pred_sqls]
        if isinstance(sol_sqls, str):
            sol_sqls = [sol_sqls]
        pred_res, pred_err, pred_to = execute_queries(pred_sqls, db_name, conn)
        sol_res, sol_err, sol_to = execute_queries(sol_sqls, db_name, conn)
        if pred_err or sol_err or pred_to or sol_to:
            return 0
        ordered = not (conditions or {}).get("order", True)
        # 比较规范化结果
        pred_norm = normalize_rows(pred_res[0] if pred_res and isinstance(pred_res[0], list) else pred_res or [])
        sol_norm = normalize_rows(sol_res[0] if sol_res and isinstance(sol_res[0], list) else sol_res or [])
        from app.eval.utils import result_sets_equal
        return 1 if result_sets_equal(sol_norm, pred_norm, order_matters=not ordered) else 0

    def preprocess_results(results, decimal_places=2):
        return [normalize_rows(r) if isinstance(r, list) else r for r in (results or [])]

    def remove_round(sql: str) -> str:
        import re as _re
        return _re.sub(r"(?i)\bround\s*\(", "CAST(", sql) if sql else sql

    return {
        "remove_distinct": remove_distinct,
        "remove_comments": remove_comments,
        "remove_order_by": remove_order_by,
        "remove_round": remove_round,
        "preprocess_results": preprocess_results,
        "preprocess_sql_for_compare": preprocess_sql_for_compare,
        "ex_base": ex_base,
        "execute_queries": execute_queries,
    }


@dataclass
class ManagementVerdict:
    """一道 Management 题的评测结果."""
    instance_id: str
    question: str
    pred_sqls: list = field(default_factory=list)
    sol_sqls: list = field(default_factory=list)
    test_case_ok: bool = False
    error: str = ""          # 执行/断言错误
    detail: str = ""         # 断言失败原因

    @property
    def ex_correct(self) -> bool:
        return self.test_case_ok

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "question": self.question,
            "test_case_ok": self.test_case_ok,
            "error": self.error,
            "detail": self.detail,
            "pred_sqls": self.pred_sqls,
            "sol_sqls": self.sol_sqls,
        }


# --------------------------------------------------------------------------- #
# execute_queries 沙箱
# --------------------------------------------------------------------------- #
def _make_execute_queries(conn: sqlite3.Connection):
    """构造官方约定的 execute_queries 沙箱函数.

    约定:
      execute_queries(sqls, db_name, conn) -> (result, error, timeout)
        sqls: str 或 list[str]
        result: list of list of rows(每条 SQL 一个结果,每行是 tuple)
        error: 非空表示有错误
        timeout: 是否超时(固定 False)
    """
    def execute_queries(sqls, db_name=None, conn=None):
        """官方约定(实测推导):
          - 传入 str 或单条 list:返回该 SQL 的 fetchall() 结果 [(row,), ...]
          - 传入多条 list     :返回 [qr1, qr2, ...](嵌套一层)
        这样 test_case 里:
            str/单条 -> result[0][0]   = value
            多条     -> result[0][0][0] = 第一条 SQL 的 value
        """
        is_list = isinstance(sqls, list)
        if isinstance(sqls, str):
            sqls = [sqls]
        results = []
        error = ""
        for sql in sqls or []:
            try:
                cur = conn.execute(sql)
                head = sql.lstrip().upper()
                if head.startswith(("SELECT", "WITH", "PRAGMA")):
                    rows = cur.fetchall()
                    results.append(list(rows))
                else:
                    conn.commit()
                    results.append([])
            except Exception as e:
                error = str(e)
                results.append([])
                break
        # str 或单条 list:返回第一条的结果(不包 list)
        if not is_list or len(sqls) <= 1:
            result = results[0] if results else []
            return (result, error, False)
        return (results, error, False)
    return execute_queries


# --------------------------------------------------------------------------- #
# 单题评测
# --------------------------------------------------------------------------- #
def evaluate_management(
    template_db: str | Path,
    *,
    instance_id: str,
    question: str,
    pred_sqls: list[str],
    sol_sqls: list[str],
    test_case_code: str,
) -> ManagementVerdict:
    """在一道 Management 题上执行 test_case.

    Args:
        template_db: 源 template.sqlite 路径(会被复制为临时隔离库).
        pred_sqls: 模型生成的 SQL 列表(依次执行).
        sol_sqls: GT 的 sol_sql(传给 test_case 供参考).
        test_case_code: 官方 test_case Python 函数源码.

    Returns:
        ManagementVerdict. 断言通过 -> test_case_ok=True.
    """
    if not test_case_code or not test_case_code.strip():
        return ManagementVerdict(
            instance_id=instance_id, question=question,
            pred_sqls=pred_sqls, sol_sqls=sol_sqls,
            test_case_ok=False, error="无 test_case 代码",
        )

    # 1. 复制临时隔离库(可写)
    tmp_dir = Path(tempfile.mkdtemp(prefix="lsb_mgmt_"))
    tmp_db = tmp_dir / "db.sqlite"
    try:
        shutil.copy2(template_db, tmp_db)
    except Exception as e:
        return ManagementVerdict(
            instance_id=instance_id, question=question,
            pred_sqls=pred_sqls, sol_sqls=sol_sqls,
            test_case_ok=False, error=f"复制数据库失败: {e}",
        )

    try:
        # 2. 可写连接 + 数学函数
        conn = sqlite3.connect(str(tmp_db))
        register_math_functions(conn)

        # 3. 执行 pred_sqls(Management 题的关键动作)
        pred_err = ""
        for sql in pred_sqls:
            if not sql or not str(sql).strip():
                continue
            try:
                conn.execute(str(sql))
                conn.commit()
            except Exception as e:
                pred_err = str(e)
                break
        if pred_err:
            conn.close()
            return ManagementVerdict(
                instance_id=instance_id, question=question,
                pred_sqls=pred_sqls, sol_sqls=sol_sqls,
                test_case_ok=False, error=f"pred_sql 执行失败: {pred_err}",
            )

        # 4. exec test_case,注入沙箱(含官方工具函数)
        helpers = _make_helpers(conn)
        # 预执行 pred_sqls 结果,供部分官方 test_case 引用(宽容处理)
        try:
            pred_qr = helpers["execute_queries"](pred_sqls, "", conn)
        except Exception:
            pred_qr = ([], "", False)
        sandbox = {
            "conn": conn,
            "date": __import__("datetime").date,
            "pred_sqls": pred_sqls,
            "sol_sqls": sol_sqls,
            "pred_query_result": pred_qr,   # 预注入,兼容官方 test_case 的隐含引用
            **helpers,  # execute_queries / remove_distinct / ex_base 等
        }
        # 官方代码: def test_case(pred_sqls, sol_sqls, db_name, conn): ...
        try:
            exec(test_case_code, sandbox)
            test_fn = sandbox.get("test_case")
            if not callable(test_fn):
                conn.close()
                return ManagementVerdict(
                    instance_id=instance_id, question=question,
                    pred_sqls=pred_sqls, sol_sqls=sol_sqls,
                    test_case_ok=False, error="test_case 未定义",
                )
            result = test_fn(pred_sqls, sol_sqls, "", conn)
            # test_case 返回 None(隐式通过)或 1(显式通过)
            passed = True
            if result is not None and result != 1 and result is not True:
                passed = False
            conn.close()
            return ManagementVerdict(
                instance_id=instance_id, question=question,
                pred_sqls=pred_sqls, sol_sqls=sol_sqls,
                test_case_ok=passed,
                error="" if passed else f"test_case 返回异常值: {result}",
            )
        except AssertionError as e:
            conn.close()
            return ManagementVerdict(
                instance_id=instance_id, question=question,
                pred_sqls=pred_sqls, sol_sqls=sol_sqls,
                test_case_ok=False, error="断言失败",
                detail=str(e)[:300],
            )
        except Exception as e:
            conn.close()
            return ManagementVerdict(
                instance_id=instance_id, question=question,
                pred_sqls=pred_sqls, sol_sqls=sol_sqls,
                test_case_ok=False, error=f"test_case 异常: {str(e)[:200]}",
                detail=traceback.format_exc()[-500:],
            )
    finally:
        # 清理临时目录
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
