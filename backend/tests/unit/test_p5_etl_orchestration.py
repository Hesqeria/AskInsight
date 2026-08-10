"""P5 ETL orchestration tests."""
import asyncio


def test_recommender_sources_exist():
    from app.agents.etl_agent.recommender import ETLModelRecommender, ETLRequirement
    rec = ETLModelRecommender()
    req = ETLRequirement("req_001", "daily GMV by region", "admin")
    result = asyncio.run(rec.recommend(req))
    assert len(result.source_tables) >= 1
    assert result.grain == "per-day-per-region"
    assert result.rationale


def test_recommender_uses_metadata():
    from app.agents.etl_agent.recommender import ETLModelRecommender, ETLRequirement
    from app.tools.metadata_query import MetadataQueryAPI
    rec = ETLModelRecommender(metadata_api=MetadataQueryAPI())
    req = ETLRequirement("req_002", "GMV trend", "admin")
    result = asyncio.run(rec.recommend(req))
    assert all("ods_" in s or "dwd_" in s for s in result.source_tables)


def test_sql_generator_ddl_partitioned():
    from app.agents.etl_agent.recommender import ETLModelRecommender, ETLRequirement
    from app.agents.etl_agent.sql_generator import ETLSQLGenerator
    rec = ETLModelRecommender()
    req = ETLRequirement("req_003", "GMV daily", "admin")
    model = asyncio.run(rec.recommend(req))
    gen = ETLSQLGenerator()
    pkg = asyncio.run(gen.generate(model))
    assert "CREATE TABLE IF NOT EXISTS" in pkg.create_ddl
    assert "PARTITION BY RANGE" in pkg.create_ddl
    assert "INSERT OVERWRITE" in pkg.insert_sql
    assert "coding_standards_applied" and len(pkg.coding_standards_applied) >= 1


def test_sql_generator_transforms():
    from app.agents.etl_agent.sql_generator import ETLSQLGenerator
    gen = ETLSQLGenerator()
    transforms = gen._default_transforms("ods_order")
    assert any(t.transform_type == "compute" for t in transforms)
    assert any(t.transform_type == "format_date" for t in transforms)


def test_etl_scheduler_cron_after_upstream():
    from app.agents.etl_agent.scheduler import ETLScheduler
    s = ETLScheduler()
    schedule = asyncio.run(s.generate_schedule("dws_gmv_daily", ["ods_order", "ods_payment"]))
    assert schedule.cron == "15 2 * * *"  # latest upstream 01:45 + 30min = 02:15
    assert "dag_ods_order" in schedule.upstream_dependencies


def test_etl_scheduler_conflicts():
    from app.agents.etl_agent.scheduler import ETLScheduler, ScheduleConfig
    s = ETLScheduler()
    warnings = asyncio.run(s.detect_conflicts(ScheduleConfig(
        cron="0 0 * * *", resource_pool="heavy")))
    assert any(w.severity == "critical" for w in warnings)


def test_quality_rules_generated():
    from app.agents.etl_agent.quality_gen import ETLQualityGenerator
    gen = ETLQualityGenerator()
    rules = asyncio.run(gen.generate("dws_gmv_daily",
                                     [{"name": "order_id", "is_primary_key": True},
                                      {"name": "dt"}]))
    types = {r.rule_type for r in rules}
    assert {"row_count_drop", "null_rate", "freshness"} <= types
    pk_rule = [r for r in rules if r.rule_type == "null_rate" and r.column == "order_id"]
    assert pk_rule and pk_rule[0].threshold["max_rate"] == 0
    assert all("dingtalk" in r.alert_channels for r in rules)


def test_draft_store_full_flow():
    from app.agents.etl_agent.recommender import ETLModelRecommender, ETLRequirement
    from app.agents.etl_agent.sql_generator import ETLSQLGenerator
    from app.agents.etl_agent.scheduler import ETLScheduler
    from app.agents.etl_agent.quality_gen import ETLQualityGenerator
    from app.agents.etl_agent.review_draft import ETLDraft, ETLDraftStore, PreflightChecker

    rec = ETLModelRecommender()
    req = ETLRequirement("req_100", "GMV by region daily", "admin")
    model = asyncio.run(rec.recommend(req))
    pkg = asyncio.run(ETLSQLGenerator().generate(model))
    sched = asyncio.run(ETLScheduler().generate_schedule("dws_gmv_daily", ["ods_order"]))
    rules = asyncio.run(ETLQualityGenerator().generate("dws_gmv_daily", []))

    store = ETLDraftStore()
    draft = ETLDraft("req_100", model, pkg, sched, rules)
    store.save(draft)

    # SQL edit
    edited = store.update_sql(draft.draft_id, "INSERT OVERWRITE TABLE dws_gmv_daily ...")
    assert "dws_gmv_daily" in edited.sql_pkg.insert_sql

    # preflight blocks bad SQL
    bad = store.get(draft.draft_id)
    bad.sql_pkg.insert_sql = "DELETE FROM dws_gmv_daily"
    res = store.submit(draft.draft_id, PreflightChecker().check)
    assert res["ok"] is False
    assert any(not c["ok"] for c in res["checks"])

    # fix and submit
    bad.sql_pkg.insert_sql = "INSERT OVERWRITE TABLE dws_gmv_daily PARTITION(dt) SELECT 1"
    res = store.submit(draft.draft_id, PreflightChecker().check)
    assert res["ok"] is True

    # rollback
    rb = store.rollback(draft.draft_id)
    assert rb["ok"] is True
    assert store.get(draft.draft_id).status == "rolled_back"
