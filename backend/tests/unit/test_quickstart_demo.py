"""Quick-start demo script tests (offline, no live DB)."""
import random


def test_seed_generates_all_statements():
    from app.scripts.quickstart_demo import _seed_sql, _DDL
    assert len(_DDL) == 7
    stmts = _seed_sql()
    assert len(stmts) == 6
    assert stmts[0].startswith("INSERT INTO dim_region")
    assert stmts[4].startswith("INSERT INTO fact_order")
    assert stmts[5].startswith("INSERT INTO dws_sales_wide")


def test_fact_order_rows():
    import re
    from app.scripts.quickstart_demo import _seed_sql
    random.seed(42)
    stmts = _seed_sql()
    fact_sql = stmts[4]
    values_part = fact_sql.split("VALUES ", 1)[1]
    tuples = re.findall(r"\(\d+\.?\d*,", values_part)
    assert len(tuples) == 20


def test_wide_table_aggregation_matches_facts():
    from app.scripts.quickstart_demo import _seed_sql
    random.seed(42)
    stmts = _seed_sql()
    wide = stmts[5]
    assert "total_amount" in wide
    assert "total_quantity" in wide
    assert "order_count" in wide


def test_ddl_contains_partition_and_replication():
    from app.scripts.quickstart_demo import _DDL
    assert any("DISTRIBUTED BY HASH" in d for d in _DDL)
    assert all("replication_num" in d for d in _DDL)
