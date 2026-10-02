"""LiveSQLBench 接入框架测试。

不依赖外部数据集:用临时构造的 SQLite 库 + jsonl 验证:
  - SqliteBackend 执行
  - 预处理(注释/DISTINCT/ORDER BY)
  - evaluate_sqlite 打分
  - LiveSQLBenchLoader 加载
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from app.eval.evaluator.sqlite_ex import (
    SqliteBackend, SqliteVerdict, evaluate_sqlite,
    remove_comments, remove_distinct, remove_order_by,
    preprocess_sql_for_compare, normalize_value, normalize_rows,
)
from app.eval.dataset.livesqlbench_loader import LiveSQLBenchLoader


# --------------------------------------------------------------------------- #
# SQL 预处理
# --------------------------------------------------------------------------- #
class TestPreprocess:
    def test_remove_comments(self):
        sql = "SELECT /* block */ a -- line\nFROM t"
        assert "/*" not in remove_comments(sql)
        assert "--" not in remove_comments(sql)
        assert "SELECT" in remove_comments(sql)

    def test_remove_comments_keeps_string(self):
        sql = "SELECT '-- not a comment'"
        assert "-- not a comment" in remove_comments(sql)

    def test_remove_distinct(self):
        assert "DISTINCT" not in remove_distinct("SELECT DISTINCT a FROM t")
        assert remove_distinct("select distinct a").upper().startswith("SELECT A")

    def test_remove_order_by(self):
        sql = "SELECT a FROM t ORDER BY a LIMIT 10"
        out = remove_order_by(sql)
        assert "ORDER BY" not in out
        assert "LIMIT" in out

    def test_remove_order_by_no_limit(self):
        sql = "SELECT a FROM t ORDER BY a"
        out = remove_order_by(sql)
        assert "ORDER BY" not in out

    def test_remove_order_by_keeps_subquery(self):
        sql = "SELECT * FROM (SELECT a FROM t ORDER BY a) WHERE x=1"
        out = remove_order_by(sql)
        # 子查询内的 ORDER BY 应保留
        assert "ORDER BY" in out

    def test_preprocess_sql_for_compare_ordered(self):
        sql = "select distinct a from t order by a"
        out = preprocess_sql_for_compare(sql, ordered=True)
        assert "DISTINCT" not in out
        assert "ORDER BY" not in out  # remove_distinct 后仍是 ordered 比较但 order 保留?

    def test_preprocess_ordered_keeps_order_by(self):
        # ordered=True 时不该去 ORDER BY
        sql = "SELECT a FROM t ORDER BY a"
        out = preprocess_sql_for_compare(sql, ordered=True)
        assert "ORDER BY" in out


class TestNormalize:
    def test_none_and_empty(self):
        assert normalize_value(None) is None
        assert normalize_value("") is None

    def test_numeric_rounded(self):
        assert normalize_value(1.23456) == 1.23
        assert normalize_value(100) == 100.0

    def test_bool_to_int(self):
        assert normalize_value(True) == 1

    def test_rows(self):
        rows = [(1, "a", None), (2.345, "b", "")]
        out = normalize_rows(rows)
        assert out[0] == (1.0, "a", None)
        assert out[1] == (2.35, "b", None)


# --------------------------------------------------------------------------- #
# SqliteBackend
# --------------------------------------------------------------------------- #
@pytest.fixture
def sample_db(tmp_path):
    """构造一个测试 SQLite 库."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE orders (id INT, amount REAL, region TEXT)")
    conn.executemany(
        "INSERT INTO orders VALUES (?,?,?)",
        [(1, 100.0, "north"), (2, 200.0, "east"), (3, 150.5, "north")],
    )
    conn.commit()
    conn.close()
    return db_path


class TestSqliteBackend:
    def test_open_and_execute(self, sample_db):
        with SqliteBackend(sample_db) as b:
            res = b.execute("SELECT SUM(amount) FROM orders")
            assert res.ok
            assert res.rows == [(450.5,)]

    def test_list_tables(self, sample_db):
        with SqliteBackend(sample_db) as b:
            assert "orders" in b.list_tables()

    def test_schema(self, sample_db):
        with SqliteBackend(sample_db) as b:
            schema = b.get_schema("orders")
            assert "orders" in schema

    def test_full_schema(self, sample_db):
        with SqliteBackend(sample_db) as b:
            assert "orders" in b.full_schema()

    def test_read_only(self, sample_db):
        """源库不被写污染."""
        with SqliteBackend(sample_db) as b:
            b.execute("INSERT INTO orders VALUES (9, 9, 'x')")  # 应失败(只读)
            res = b.execute("SELECT COUNT(*) FROM orders")
            assert res.rows[0][0] == 3

    def test_execute_error(self, sample_db):
        with SqliteBackend(sample_db) as b:
            res = b.execute("SELECT * FROM nonexistent")
            assert not res.ok
            assert "error" in res.error or res.error


# --------------------------------------------------------------------------- #
# evaluate_sqlite
# --------------------------------------------------------------------------- #
class TestEvaluateSqlite:
    def test_exact_match(self, sample_db):
        with SqliteBackend(sample_db) as b:
            v = evaluate_sqlite(
                b, instance_id="t1", question="q",
                pred_sql="SELECT SUM(amount) FROM orders",
                sol_sql="SELECT SUM(amount) FROM orders",
            )
            assert v.ex_correct is True

    def test_equivalent_sql_match(self, sample_db):
        """不同写法同结果."""
        with SqliteBackend(sample_db) as b:
            v = evaluate_sqlite(
                b, instance_id="t1", question="q",
                pred_sql="SELECT SUM(amount) FROM orders",
                sol_sql="SELECT SUM(amount) FROM orders WHERE region != ''",
            )
            assert v.ex_correct is True

    def test_mismatch(self, sample_db):
        with SqliteBackend(sample_db) as b:
            v = evaluate_sqlite(
                b, instance_id="t1", question="q",
                pred_sql="SELECT SUM(amount) FROM orders WHERE region='north'",
                sol_sql="SELECT SUM(amount) FROM orders",
            )
            assert v.ex_correct is False

    def test_order_sensitive(self, sample_db):
        """ordered=True 时结果顺序敏感."""
        with SqliteBackend(sample_db) as b:
            v = evaluate_sqlite(
                b, instance_id="t1", question="q",
                pred_sql="SELECT id FROM orders ORDER BY id DESC",
                sol_sql="SELECT id FROM orders ORDER BY id ASC",
                ordered=True,
            )
            # 顺序不同 → 不匹配(rows: [3,2,1] vs [1,2,3])
            assert v.ex_correct is False

    def test_order_insensitive(self, sample_db):
        with SqliteBackend(sample_db) as b:
            v = evaluate_sqlite(
                b, instance_id="t1", question="q",
                pred_sql="SELECT id FROM orders ORDER BY id DESC",
                sol_sql="SELECT id FROM orders ORDER BY id ASC",
                ordered=False,
            )
            assert v.ex_correct is True

    def test_empty_pred(self, sample_db):
        with SqliteBackend(sample_db) as b:
            v = evaluate_sqlite(
                b, instance_id="t1", question="q",
                pred_sql="", sol_sql="SELECT 1",
            )
            assert v.ex_correct is False
            assert "空" in v.error

    def test_pred_exec_error(self, sample_db):
        with SqliteBackend(sample_db) as b:
            v = evaluate_sqlite(
                b, instance_id="t1", question="q",
                pred_sql="SELECT * FROM bad_table",
                sol_sql="SELECT * FROM orders",
            )
            assert v.ex_correct is False

    def test_float_rounding(self, sample_db):
        """数值 round 2 位后,150.5 vs 150.50 等价."""
        with SqliteBackend(sample_db) as b:
            v = evaluate_sqlite(
                b, instance_id="t1", question="q",
                pred_sql="SELECT amount FROM orders WHERE id=3",
                sol_sql="SELECT amount FROM orders WHERE id=3",
            )
            assert v.ex_correct is True

    def test_to_dict_serializable(self, sample_db):
        with SqliteBackend(sample_db) as b:
            v = evaluate_sqlite(
                b, instance_id="t1", question="q",
                pred_sql="SELECT 1", sol_sql="SELECT 1",
            )
            d = v.to_dict()
            assert d["instance_id"] == "t1"
            assert d["ex_correct"] is True


# --------------------------------------------------------------------------- #
# LiveSQLBenchLoader
# --------------------------------------------------------------------------- #
@pytest.fixture
def sample_dataset(tmp_path):
    """构造一个迷你 LiveSQLBench 目录结构."""
    root = tmp_path / "livesqlbench"
    dbs = root / "databases"
    dbs.mkdir(parents=True)

    # 一个 SQLite 库
    db_path = dbs / "shop.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE products (id INT, name TEXT, price REAL)")
    conn.execute("INSERT INTO products VALUES (1, 'apple', 1.5)")
    conn.commit()
    conn.close()

    # 题目 jsonl
    tasks = [
        {
            "instance_id": "shop_task_1",
            "selected_database": "shop",
            "query": "What is the total price of all products?",
            "sol_sql": "SELECT SUM(price) FROM products",
            "category": "Query",
            "difficulty_tier": "Simple",
            "external_knowledge": [],
        },
        {
            "instance_id": "shop_task_2",
            "selected_database": "shop",
            "query": "List product names ordered by price",
            "sol_sql": "SELECT name FROM products ORDER BY price DESC",
            "category": "Query",
            "difficulty_tier": "Moderate",
            "external_knowledge": [],
        },
    ]
    with open(root / "livesqlbench_data.jsonl", "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t) + "\n")

    # 列含义 + HKB
    shop_dir = dbs / "shop"
    shop_dir.mkdir(parents=True, exist_ok=True)
    cm = {"products": {"name": "product name", "price": "unit price"}}
    with open(shop_dir / "column_meaning.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(cm))
    hkb_dir = shop_dir / "hkb"
    hkb_dir.mkdir(parents=True, exist_ok=True)
    with open(hkb_dir / "rules.json", "w", encoding="utf-8") as f:
        json.dump([{"name": "price_rule", "definition": "price is in USD"}], f)

    return root


class TestLiveSQLBenchLoader:
    def test_load_tasks(self, sample_dataset):
        loader = LiveSQLBenchLoader(sample_dataset)
        tasks = loader.load_tasks()
        assert len(tasks) == 2
        assert tasks[0].instance_id == "shop_task_1"
        assert tasks[0].database == "shop"
        assert tasks[0].category == "Query"

    def test_load_tasks_limit(self, sample_dataset):
        loader = LiveSQLBenchLoader(sample_dataset)
        tasks = loader.load_tasks(limit=1)
        assert len(tasks) == 1

    def test_load_tasks_category_filter(self, sample_dataset):
        loader = LiveSQLBenchLoader(sample_dataset)
        tasks = loader.load_tasks(category="Management")
        assert tasks == []

    def test_find_db(self, sample_dataset):
        loader = LiveSQLBenchLoader(sample_dataset)
        p = loader.find_db_file("shop")
        assert p is not None
        assert p.name == "shop.db"

    def test_find_db_missing(self, sample_dataset):
        loader = LiveSQLBenchLoader(sample_dataset)
        assert loader.find_db_file("nope") is None

    def test_build_context(self, sample_dataset):
        loader = LiveSQLBenchLoader(sample_dataset)
        ctx = loader.build_context("shop")
        assert ctx["db_file"]
        assert "products" in ctx["ddl"]
        assert "products" in ctx["tables"]
        assert "unit price" in ctx["column_meanings"]
        assert "USD" in ctx["hkb"]

    def test_missing_data_root_raises(self, tmp_path):
        loader = LiveSQLBenchLoader(tmp_path / "empty")
        with pytest.raises(FileNotFoundError):
            loader.load_tasks()
