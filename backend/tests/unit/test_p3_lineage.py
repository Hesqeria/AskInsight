"""P3: sqlglot lineage engine tests."""
from app.core.sql_lineage_engine import extract_lineage_sqlglot
from app.agent.nodes.extract_lineage import extract_lineage_from_sql


def test_aggregate_with_alias():
    rows = extract_lineage_from_sql(
        "SELECT SUM(t.gmv) AS gmv_total FROM dw.ads_gmv_total_day AS t")
    assert rows == [{"target_column": "gmv_total",
                     "source_table": "dw.ads_gmv_total_day",
                     "source_column": "gmv",
                     "transformation": "SUM"}]


def test_join_resolves_aliases():
    rows = extract_lineage_from_sql(
        "SELECT o.payer_count FROM dw.dwd_order_info_inc o "
        "JOIN dw.dim_region r ON o.region_id = r.region_id")
    assert rows[0]["source_table"] == "dw.dwd_order_info_inc"
    assert rows[0]["transformation"] == "DIRECT"


def test_case_when():
    rows = extract_lineage_from_sql(
        "SELECT CASE WHEN t.gmv > 100 THEN 1 ELSE 0 END AS lvl "
        "FROM dw.ads_gmv_total_day t")
    assert rows[0]["transformation"] == "CASE"
    assert rows[0]["source_column"] == "gmv"


def test_cte_excluded_from_tables():
    rows = extract_lineage_from_sql(
        "WITH x AS (SELECT gmv FROM dw.ads_gmv_total_day) "
        "SELECT SUM(gmv) AS s FROM x")
    assert rows[0]["target_column"] == "s"
    assert all(r["source_table"] != "x" for r in rows)


def test_unparseable_returns_empty_not_crash():
    assert extract_lineage_from_sql("not a sql at all ###") == []


def test_ddl_returns_empty():
    assert extract_lineage_from_sql("CREATE TABLE x (a INT)") == []


def test_engine_returns_none_on_parse_error():
    assert extract_lineage_sqlglot("### bad ###") is None
