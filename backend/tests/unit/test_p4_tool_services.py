"""P4 Tool Service layer tests."""


def test_sql_validate_select_ok():
    from app.tools.sql_toolkit import SQLToolkit
    tk = SQLToolkit()
    v = tk.validate_sql("SELECT gmv FROM dws_gmv_daily WHERE dt = '2024-01-01'", "L2_analyst")
    assert v.syntax_ok is True
    assert v.business_rule_ok is True
    assert v.data_size_ok is True
    assert v.ok is True


def test_sql_validate_blocks_insert():
    from app.tools.sql_toolkit import SQLToolkit
    tk = SQLToolkit()
    v = tk.validate_sql("INSERT INTO dws_gmv_daily VALUES (1)", "L4_admin")
    assert v.ok is False
    assert any("仅允许 SELECT" in e for e in v.errors)


def test_sql_validate_permission():
    from app.tools.sql_toolkit import SQLToolkit
    tk = SQLToolkit()
    v = tk.validate_sql("SELECT gmv FROM ads_gmv_overview", "L1_business")
    assert v.business_rule_ok is False
    assert any("无权访问" in e for e in v.errors)


def test_sql_validate_scan_rows():
    from app.tools.sql_toolkit import SQLToolkit
    tk = SQLToolkit()
    v = tk.validate_sql("SELECT * FROM ods_order", "L3_engineer")
    assert v.data_size_ok is False


def test_sql_complexity():
    from app.tools.sql_toolkit import SQLToolkit
    tk = SQLToolkit()
    assert tk.classify_complexity("SELECT a FROM t1") == "simple"
    assert tk.classify_complexity("SELECT a FROM t1 JOIN t2 ON x JOIN t3 ON y") == "medium"
    assert tk.classify_complexity("SELECT a FROM t1 JOIN t2 ON x JOIN t3 ON y JOIN t4 ON z JOIN t5 ON w") == "complex"


def test_metadata_tables_role_filter():
    from app.tools.metadata_query import get_metadata_api
    api = get_metadata_api()
    all_tables = api.list_tables(role="L4_admin")
    l1_tables = api.list_tables(role="L1_business")
    assert len(all_tables) >= len(l1_tables)
    assert len(l1_tables) >= 2


def test_metadata_semantic_search():
    from app.tools.metadata_query import get_metadata_api
    api = get_metadata_api()
    res = api.search_columns_by_semantic("payment_amount", top_k=3)
    assert len(res) >= 1
    assert res[0]["name"] == "payment_amount"


def test_metadata_lineage():
    from app.tools.metadata_query import get_metadata_api
    api = get_metadata_api()
    up = api.get_lineage("dws_gmv_daily", direction="upstream")
    assert "dwd_order_detail" in up["nodes"]


def test_metadata_cache():
    from app.tools.metadata_query import MetadataQueryAPI
    api = MetadataQueryAPI()
    t1 = api.get_table("dwd_order_detail")
    assert t1 is not None
    api._cache_store.clear()
    # cached value should still resolve via builtins if not cached; verify cache path
    api._cache_set("meta:table:dwd_order_detail", t1)
    assert api._cache_get("meta:table:dwd_order_detail", 3600) is t1


def test_profiler_count_and_distribution():
    from app.tools.profiler import DataProfiler
    p = DataProfiler()
    assert p.count_rows("ods_order") == 2000000
    dist = p.value_distribution("ods_order", "status", top_n=2)
    assert len(dist) == 2
    assert dist[0]["value"] == "A"


def test_profiler_table_profile_cache():
    from app.tools.profiler import DataProfiler
    p = DataProfiler()
    tp = p.profile_table("dws_gmv_daily")
    assert tp.row_count == 365
    assert "dt" in tp.column_profiles
    cached = p.profile_table("dws_gmv_daily")
    assert cached is tp  # cache hit returns same object


def test_scheduler_conflict_detection():
    from app.tools.scheduler import AirflowScheduler
    s = AirflowScheduler()
    conflicts = s.detect_schedule_conflict("0 0 * * *", "dag_etl_daily")
    assert len(conflicts) == 1
    assert s.detect_schedule_conflict("0 3 * * *", "other") == []


def test_notifier_audit():
    from app.tools.notifier import Notifier
    import asyncio
    n = Notifier()
    asyncio.run(n.notify("dingtalk", ["u1"], "告警", "GMV下降", severity="P1"))
    assert len(n.get_audit_log()) == 1
    assert n.get_audit_log()[0]["severity"] == "P1"


def test_notifier_push_ws_no_redis():
    from app.tools.notifier import Notifier
    import asyncio
    n = Notifier()
    asyncio.run(n.push_ws("u1", {"msg": "hi"}))  # no redis -> no-op


def test_log_analyzer_masking():
    from app.tools.log_analyzer import LogAnalyzer
    la = LogAnalyzer()
    masked = la._mask("password=secret123 token=abc api_key=def")
    assert "secret123" not in masked
    assert "abc" not in masked


def test_log_analyzer_audit_filter():
    from app.tools.log_analyzer import LogAnalyzer
    la = LogAnalyzer()
    rows = la.query_audit_logs(request_id="req-0001")
    assert rows[0]["request_id"] == "req-0001"


def test_log_analyzer_airflow():
    from app.tools.log_analyzer import LogAnalyzer
    la = LogAnalyzer()
    log = la.query_airflow_logs("dag_etl_daily", "run-1")
    assert "dag_etl_daily" in log