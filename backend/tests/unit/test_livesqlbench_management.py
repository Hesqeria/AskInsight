"""Management(CRUD)类评测器测试.

验证:
  - execute_queries 沙箱(str vs list 返回结构)
  - evaluate_management 通过/失败判定
  - 隔离数据库(不污染源)
  - 数学函数在 Management 评测中可用
"""
import sqlite3
from pathlib import Path

import pytest

from app.eval.evaluator.management_ex import (
    ManagementVerdict, evaluate_management, _make_execute_queries,
)


@pytest.fixture
def sample_db(tmp_path):
    """构造 Management 可写测试库."""
    db_path = tmp_path / "mgmt.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (id INT, val INT)")
    conn.executemany("INSERT INTO t VALUES (?,?)", [(1, 10), (2, 20), (3, 5)])
    conn.commit()
    conn.close()
    return db_path


class TestExecuteQueries:
    def test_str_input_returns_rows(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        eq = _make_execute_queries(conn)
        result, err, to = eq("SELECT COUNT(*) FROM t", "db", conn)
        assert err == ""
        # str: result[0] 是行,result[0][0] 是值
        assert result[0][0] == 3
        conn.close()

    def test_single_list_input_returns_rows(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        eq = _make_execute_queries(conn)
        result, err, to = eq(["SELECT COUNT(*) FROM t"], "db", conn)
        # 单条 list: result[0][0] 是值(与 str 输入一致,不包 list)
        assert result[0][0] == 3
        conn.close()

    def test_multi_list_input_returns_nested(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        eq = _make_execute_queries(conn)
        result, err, to = eq(
            ["SELECT COUNT(*) FROM t", "SELECT SUM(val) FROM t"], "db", conn)
        # 多条: result[0] 是第一条结果
        assert result[0][0][0] == 3
        conn.close()

    def test_ddl_returns_empty(self, sample_db):
        conn = sqlite3.connect(str(sample_db))
        eq = _make_execute_queries(conn)
        result, err, to = eq("CREATE TABLE x (a INT)", "db", conn)
        assert err == ""
        conn.close()


class TestEvaluateManagement:
    def test_update_with_assertion_passes(self, sample_db):
        """模拟 alien_M_1:UPDATE + 验证断言."""
        code = '''def test_case(pred_sqls, sol_sqls, db_name, conn):
    verification_sql = "SELECT COUNT(*) FROM t WHERE val < 10"
    pred_result, _, _ = execute_queries(verification_sql, db_name, conn)
    assert pred_result[0][0] == 0
'''
        v = evaluate_management(
            sample_db, instance_id="m1", question="q",
            pred_sqls=["UPDATE t SET val = val + 100 WHERE val < 10"],
            sol_sqls=[], test_case_code=code,
        )
        assert v.test_case_ok is True

    def test_update_wrong_assertion_fails(self, sample_db):
        code = '''def test_case(pred_sqls, sol_sqls, db_name, conn):
    pred_result, _, _ = execute_queries("SELECT COUNT(*) FROM t WHERE val < 10", db_name, conn)
    assert pred_result[0][0] == 0
'''
        # pred 没改数据 -> 断言失败
        v = evaluate_management(
            sample_db, instance_id="m1", question="q",
            pred_sqls=["SELECT * FROM t"], sol_sqls=[], test_case_code=code,
        )
        assert v.test_case_ok is False
        assert "断言失败" in v.error

    def test_isolated_database(self, sample_db):
        """评测不应污染源库."""
        code = '''def test_case(pred_sqls, sol_sqls, db_name, conn):
    pred_result, _, _ = execute_queries("SELECT COUNT(*) FROM t", db_name, conn)
    assert pred_result[0][0] == 3
'''
        v = evaluate_management(
            sample_db, instance_id="m1", question="q",
            pred_sqls=["UPDATE t SET val = 0"], sol_sqls=[], test_case_code=code,
        )
        assert v.test_case_ok is True
        # 源库未被污染
        conn = sqlite3.connect(str(sample_db))
        cnt = conn.execute("SELECT COUNT(*) FROM t WHERE val = 0").fetchone()[0]
        conn.close()
        assert cnt == 0

    def test_math_functions_available(self, sample_db):
        code = '''def test_case(pred_sqls, sol_sqls, db_name, conn):
    pred_result, _, _ = execute_queries("SELECT LOG10(1000), POWER(2,3), SQRT(16)", db_name, conn)
    assert abs(pred_result[0][0] - 3.0) < 1e-9
    assert pred_result[0][1] == 8
    assert pred_result[0][2] == 4
'''
        v = evaluate_management(
            sample_db, instance_id="m1", question="q",
            pred_sqls=["SELECT 1"], sol_sqls=[], test_case_code=code,
        )
        assert v.test_case_ok is True

    def test_pred_sql_error_returns_fail(self, sample_db):
        code = 'def test_case(pred_sqls, sol_sqls, db_name, conn):\n    return 1\n'
        v = evaluate_management(
            sample_db, instance_id="m1", question="q",
            pred_sqls=["SELECT * FROM nonexistent"], sol_sqls=[],
            test_case_code=code,
        )
        assert v.test_case_ok is False
        assert "pred_sql 执行失败" in v.error

    def test_no_test_case(self, sample_db):
        v = evaluate_management(
            sample_db, instance_id="m1", question="q",
            pred_sqls=["SELECT 1"], sol_sqls=[], test_case_code="",
        )
        assert v.test_case_ok is False
        assert "无 test_case" in v.error

    def test_assertion_detail_captured(self, sample_db):
        code = '''def test_case(pred_sqls, sol_sqls, db_name, conn):
    assert False, "custom failure message"
'''
        v = evaluate_management(
            sample_db, instance_id="m1", question="q",
            pred_sqls=["SELECT 1"], sol_sqls=[], test_case_code=code,
        )
        assert v.test_case_ok is False
        assert "custom failure message" in v.detail

    def test_to_dict_serializable(self, sample_db):
        v = ManagementVerdict(
            instance_id="m1", question="q",
            pred_sqls=["SELECT 1"], sol_sqls=[], test_case_ok=True,
        )
        d = v.to_dict()
        assert d["instance_id"] == "m1"
        assert d["test_case_ok"] is True


class TestListCompat:
    def test_remove_distinct_list(self):
        from app.eval.evaluator.sqlite_ex import remove_distinct
        assert remove_distinct(["SELECT DISTINCT a FROM t"]) == ["SELECT a FROM t"]

    def test_remove_comments_list(self):
        from app.eval.evaluator.sqlite_ex import remove_comments
        out = remove_comments(["SELECT -- c\n 1"])
        assert "--" not in out[0]

    def test_pred_query_result_preinjected(self, sample_db):
        """宽容沙箱:预注入 pred_query_result 兼容官方隐含引用."""
        code = '''def test_case(pred_sqls, sol_sqls, db_name, conn):
    assert pred_query_result is not None
    return 1
'''
        v = evaluate_management(
            sample_db, instance_id="m1", question="q",
            pred_sqls=["SELECT 1"], sol_sqls=[], test_case_code=code,
        )
        assert v.test_case_ok is True

    def test_remove_distinct_in_test_case(self, sample_db):
        """官方 test_case 内调用 remove_distinct(list)."""
        code = '''def test_case(pred_sqls, sol_sqls, db_name, conn):
    cleaned = remove_distinct(pred_sqls)
    return 1
'''
        v = evaluate_management(
            sample_db, instance_id="m1", question="q",
            pred_sqls=["SELECT DISTINCT id FROM t"], sol_sqls=[], test_case_code=code,
        )
        assert v.test_case_ok is True
