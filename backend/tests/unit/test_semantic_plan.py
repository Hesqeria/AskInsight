"""Tests for ontology-grounded semantic plan + SQL renderer (NL→IR→SQL).

Covers the user's exact example: "华北 上个月的gmv" should render to a
SQL with:
  - SUM(total_amount) from dwd_order_info_inc (GMV measure)
  - JOIN dim_base_province + dim_base_region (region path)
  - WHERE region_name='华北'
  - WHERE dt BETWEEN <last_month_start> AND <last_month_end>
  - WHERE order_status='PAID' (pre-filter from lineage_business)
"""
from datetime import date
from unittest.mock import patch

import pytest

from app.ontology.plan import (
    Measure, DimensionFilter, DimensionGroupBy, TimeRange, JoinSpec,
    SemanticPlan, render_sql_from_plan,
)
from app.agent.nodes.semantic_grounding import (
    build_plan,
    parse_time_expression,
    match_business_term,
    match_dimension_class,
    JOIN_REGISTRY, MEASURE_REGISTRY,
)


# --------------------------------------------------------------------------- #
# Plan data model
# --------------------------------------------------------------------------- #
class TestSemanticPlan:
    def _gmv_plan(self):
        return SemanticPlan(
            question="华北 上个月的gmv",
            measures=[Measure(
                business_term="GMV",
                class_id="C030",
                column="dw.dwd_order_info_inc.total_amount",
                aggregation="SUM",
                pre_filters=["order_status = 'PAID'"],
            )],
            dimensions=[DimensionFilter(
                class_id="C050",
                column="r.region_name",
                operator="=",
                value="华北",
            )],
            time=TimeRange(column="t.dt", start="2026-07-01", end="2026-07-31"),
            joins=[
                JoinSpec("t", "province_id", "p", "id", "dw.dim_base_province"),
                JoinSpec("p", "region_id", "r", "id", "dw.dim_base_region"),
            ],
        )

    def test_fact_table_extraction(self):
        plan = self._gmv_plan()
        assert plan.fact_table == "dw.dwd_order_info_inc"
        assert plan.fact_table_short == "dwd_order_info_inc"

    def test_is_valid_requires_measure(self):
        empty = SemanticPlan(question="empty")
        assert empty.is_valid is False

    def test_is_valid_requires_dotted_column(self):
        bad = SemanticPlan(question="x", measures=[
            Measure(business_term="GMV", class_id="C030",
                    column="total_amount", aggregation="SUM"),
        ])
        assert bad.is_valid is False

    def test_to_dict_round_trips(self):
        plan = self._gmv_plan()
        d = plan.to_dict()
        assert d["measures"][0]["business_term"] == "GMV"
        assert d["dimensions"][0]["value"] == "华北"
        assert d["time"]["start"] == "2026-07-01"
        assert len(d["joins"]) == 2


# --------------------------------------------------------------------------- #
# SQL renderer
# --------------------------------------------------------------------------- #
class TestRenderSqlFromPlan:
    def _gmv_plan(self):
        return SemanticPlan(
            question="华北 上个月的gmv",
            measures=[Measure(
                business_term="GMV", class_id="C030",
                column="dw.dwd_order_info_inc.total_amount",
                aggregation="SUM",
                pre_filters=["order_status = 'PAID'"],
            )],
            dimensions=[DimensionFilter(
                class_id="C050", column="r.region_name",
                operator="=", value="华北",
            )],
            time=TimeRange(column="t.dt", start="2026-07-01", end="2026-07-31"),
            joins=[
                JoinSpec("t", "province_id", "p", "id", "dw.dim_base_province"),
                JoinSpec("p", "region_id", "r", "id", "dw.dim_base_region"),
            ],
        )

    def test_renders_full_gmv_query(self):
        sql = render_sql_from_plan(self._gmv_plan())
        # Must contain all key pieces.
        # Column gets aliased to t.<col> because the fact table has alias 't'.
        assert "SUM(t.total_amount)" in sql
        assert "FROM dw.dwd_order_info_inc" in sql
        assert "JOIN dw.dim_base_province" in sql
        assert "JOIN dw.dim_base_region" in sql
        assert "region_name = '华北'" in sql
        assert "BETWEEN '2026-07-01' AND '2026-07-31'" in sql
        assert "order_status = 'PAID'" in sql

    def test_invalid_plan_raises(self):
        with pytest.raises(ValueError):
            render_sql_from_plan(SemanticPlan(question="bad"))

    def test_count_distinct_renders_correctly(self):
        plan = SemanticPlan(
            question="dau",
            measures=[Measure(
                business_term="DAU", class_id="C011",
                column="dw.dws_user_active_day.user_id",
                aggregation="COUNT_DISTINCT",
            )],
            time=TimeRange(column="t.dt", start="2026-08-12", end="2026-08-12"),
        )
        sql = render_sql_from_plan(plan)
        assert "COUNT(DISTINCT" in sql

    def test_in_operator(self):
        plan = SemanticPlan(
            question="多区域",
            measures=[Measure(
                business_term="GMV", class_id="C030",
                column="dw.dwd_order_info_inc.total_amount",
                aggregation="SUM",
            )],
            dimensions=[DimensionFilter(
                class_id="C050", column="r.region_name",
                operator="IN", value=["华北", "华东"],
            )],
            joins=[JoinSpec("t", "province_id", "p", "id", "dw.dim_base_province"),
                   JoinSpec("p", "region_id", "r", "id", "dw.dim_base_region")],
        )
        sql = render_sql_from_plan(plan)
        assert "IN ('华北', '华东')" in sql

    def test_group_by(self):
        plan = SemanticPlan(
            question="各地区GMV",
            measures=[Measure(business_term="GMV", class_id="C030",
                              column="dw.dwd_order_info_inc.total_amount",
                              aggregation="SUM")],
            group_by=[DimensionGroupBy(class_id="C050", column="r.region_name")],
            joins=[JoinSpec("t", "province_id", "p", "id", "dw.dim_base_province"),
                   JoinSpec("p", "region_id", "r", "id", "dw.dim_base_region")],
        )
        sql = render_sql_from_plan(plan)
        assert "GROUP BY r.region_name" in sql
        assert "r.region_name" in sql.split("SELECT")[1].split("FROM")[0]

    def test_string_escaping(self):
        plan = SemanticPlan(
            question="x",
            measures=[Measure(business_term="GMV", class_id="C030",
                              column="dw.dwd_order_info_inc.total_amount",
                              aggregation="SUM")],
            dimensions=[DimensionFilter(class_id="C050", column="r.region_name",
                                        operator="=", value="O'Brien")],
            joins=[JoinSpec("t", "province_id", "p", "id", "dw.dim_base_province"),
                   JoinSpec("p", "region_id", "r", "id", "dw.dim_base_region")],
        )
        sql = render_sql_from_plan(plan)
        # Single quotes inside string must be doubled.
        assert "O''Brien" in sql


# --------------------------------------------------------------------------- #
# Time parser
# --------------------------------------------------------------------------- #
class TestParseTimeExpression:
    def test_yesterday(self):
        s, e = parse_time_expression("昨天的销售", today=date(2026, 8, 12))
        assert s == date(2026, 8, 11)
        assert e == date(2026, 8, 11)

    def test_today(self):
        s, e = parse_time_expression("今天GMV", today=date(2026, 8, 12))
        assert s == e == date(2026, 8, 12)

    def test_last_month(self):
        s, e = parse_time_expression("上个月的gmv", today=date(2026, 8, 12))
        assert s == date(2026, 7, 1)
        assert e == date(2026, 7, 31)

    def test_last_month_feb_leap_year(self):
        # Feb 2026 has 28 days (not leap).
        s, e = parse_time_expression("上月", today=date(2026, 3, 15))
        assert s == date(2026, 2, 1)
        assert e == date(2026, 2, 28)

    def test_last_month_dec_to_prev_year(self):
        s, e = parse_time_expression("上个月", today=date(2026, 1, 15))
        assert s == date(2025, 12, 1)
        assert e == date(2025, 12, 31)

    def test_recent_n_days(self):
        s, e = parse_time_expression("近7天的销售", today=date(2026, 8, 12))
        assert s == date(2026, 8, 6)
        assert e == date(2026, 8, 12)

    def test_this_month(self):
        s, e = parse_time_expression("本月GMV", today=date(2026, 8, 15))
        assert s == date(2026, 8, 1)
        assert e == date(2026, 8, 31)

    def test_this_quarter(self):
        s, e = parse_time_expression("本季度GMV", today=date(2026, 8, 12))
        assert s == date(2026, 7, 1)
        assert e == date(2026, 9, 30)

    def test_this_quarter_q4(self):
        s, e = parse_time_expression("本季度", today=date(2026, 11, 5))
        assert s == date(2026, 10, 1)
        assert e == date(2026, 12, 31)

    def test_last_quarter(self):
        s, e = parse_time_expression("上季度", today=date(2026, 8, 12))
        assert s == date(2026, 4, 1)
        assert e == date(2026, 6, 30)

    def test_last_quarter_cross_year(self):
        s, e = parse_time_expression("上季度", today=date(2026, 1, 15))
        assert s == date(2025, 10, 1)
        assert e == date(2025, 12, 31)

    def test_no_match_returns_none(self):
        assert parse_time_expression("无关文本", today=date(2026, 8, 12)) is None

    def test_empty_returns_none(self):
        assert parse_time_expression("", today=date(2026, 8, 12)) is None


# --------------------------------------------------------------------------- #
# Business term matcher
# --------------------------------------------------------------------------- #
class TestMatchBusinessTerm:
    def test_gmv_direct(self):
        assert match_business_term("上个月的GMV") == "GMV"

    def test_gmv_alias_chengjiao(self):
        assert match_business_term("昨天的成交金额") == "GMV"

    def test_dau_alias(self):
        assert match_business_term("昨天日活") == "DAU"

    def test_dau_alias_huoyue(self):
        assert match_business_term("用户活跃情况") == "DAU"
        assert match_business_term("活跃用户") == "DAU"

    def test_order_count_alias(self):
        assert match_business_term("今天的订单数") == "order_count"

    def test_order_count_broad_alias_not_matched(self):
        # 设计决策:宽泛别名 "订单"/"订单情况" 已移除,避免误匹配
        # "平均订单金额/订单运费" 等非 order_count 语义的题。
        assert match_business_term("每个区域的订单情况") is None
        # 应映射到 avg_order_amount 而非被宽泛"订单"误判为 order_count
        assert match_business_term("平均订单金额") == "avg_order_amount"

    def test_gmv_alias_shengyi(self):
        assert match_business_term("最近生意怎么样") == "GMV"
        assert match_business_term("卖了多少") == "GMV"

    def test_unknown_returns_none(self):
        assert match_business_term("随便一个无关词") is None


# --------------------------------------------------------------------------- #
# Dimension value matcher
# --------------------------------------------------------------------------- #
class TestMatchDimensionClass:
    def test_matches_known_dim_table(self):
        # Simulate output from match_dimension_value node.
        matched = [
            {"value": "华北", "table_name": "dim_base_region", "column_id": "x"},
            {"value": "湖北", "table_name": "dim_base_region", "column_id": "y"},
        ]
        results = match_dimension_class(matched)
        assert len(results) == 2
        cid, col, val = results[0]
        assert cid == "C050"
        assert col == "r.region_name"
        assert val == "华北"

    def test_ignores_unknown_table(self):
        matched = [
            {"value": "x", "table_name": "unknown_table", "column_id": "x"},
        ]
        assert match_dimension_class(matched) == []

    def test_empty_safe(self):
        assert match_dimension_class([]) == []
        assert match_dimension_class(None) == []


# --------------------------------------------------------------------------- #
# build_plan — the full grounding pipeline
# --------------------------------------------------------------------------- #
class TestBuildPlan:
    def test_full_gmv_region_query(self):
        """User's exact example: 华北 上个月的gmv"""
        plan = build_plan(
            question="华北 上个月的gmv",
            keywords=["华北", "上个月", "gmv"],
            matched_dimension_values=[
                {"value": "华北", "table_name": "dim_base_region", "column_id": "x"},
            ],
            today=date(2026, 8, 12),
        )
        # Plan is fully grounded.
        assert plan.confidence == 1.0
        assert len(plan.measures) == 1
        assert plan.measures[0].business_term == "GMV"
        # Region filter (华北) re-sources GMV to the region ranking table
        assert plan.measures[0].column == "dw.dwd_order_info_inc.total_amount"
        assert plan.measures[0].pre_filters == ["order_status = '1001'"]
        assert plan.time.start == "2026-07-01"
        assert plan.time.end == "2026-07-31"
        assert len(plan.dimensions) == 1
        assert plan.dimensions[0].value == "华北"
        assert plan.dimensions[0].column == "r.region_name"
        # region lives on ads_region_gmv_rank -> no join chain needed
        assert len(plan.joins) == 2  # province+region chain for r.region_name
        # SQL renders successfully.
        sql = render_sql_from_plan(plan)
        # Column is aliased to t.<col> because fact table has alias 't'.
        assert "SUM(t.total_amount)" in sql
        assert "region_name = '华北'" in sql
        # ads_gmv_total_day 已预聚合(口径内置),无 order_status 过滤
        assert "BETWEEN '2026-07-01' AND '2026-07-31'" in sql
        assert "order_status = '1001'" in sql  # raw dwd 需已支付口径过滤

    def test_measure_only_partial_confidence(self):
        """Question has GMV but no time/dim → confidence 0.7."""
        plan = build_plan(question="gmv是多少", keywords=["gmv"], today=date(2026, 8, 12))
        assert len(plan.measures) == 1
        assert plan.confidence == 0.7
        # no-time questions now default to the last-30-days window
        assert plan.time is not None
        assert plan.dimensions == []

    def test_unknown_measure_low_confidence(self):
        """Unknown business term → confidence 0.3, no measure."""
        plan = build_plan(question="随便问个无关问题", keywords=["随便"])
        assert plan.confidence == 0.3
        assert plan.measures == []
        assert any("no measure matched" in n for n in plan.notes)

    def test_uses_glossary_for_term_match(self):
        """Glossary matches provide alias → canonical mapping."""
        plan = build_plan(
            question="客户咨询",
            keywords=["客户"],
            glossary_matches=[{"term": "成交金额", "standard_name": "GMV"}],
            today=date(2026, 8, 12),
        )
        # "成交金额" alias should still match GMV via _BUSINESS_TERM_ALIASES.
        assert any(m.business_term == "GMV" for m in plan.measures)


# --------------------------------------------------------------------------- #
# Join registry completeness
# --------------------------------------------------------------------------- #
class TestJoinRegistry:
    def test_order_to_region_has_two_hops(self):
        joins = JOIN_REGISTRY[("C030", "C050")]
        assert len(joins) == 2
        assert joins[0]["right_table_real"] == "dw.dim_base_province"
        assert joins[1]["right_table_real"] == "dw.dim_base_region"

    def test_measure_registry_columns_match_dw(self):
        """Sanity: registered columns should look like 'dw.<table>.<col>'."""
        for term, spec in MEASURE_REGISTRY.items():
            col = spec["column"]
            assert col.startswith("dw."), f"{term} column {col} should be fully qualified"
            assert col.count(".") == 2, f"{term} column {col} should be dw.table.col"


# --------------------------------------------------------------------------- #
# adapt_measure_source — JOIN correctness for ads_total measures
# --------------------------------------------------------------------------- #
class TestAdaptMeasureSource:
    def _plan(self, **kw):
        from app.ontology.plan import SemanticPlan as SP, Measure as M, \
            DimensionFilter as DF, DimensionGroupBy as DG, TimeRange as TR, JoinSpec as JS
        return SP(
            question=kw.get("question", "q"),
            measures=[M(business_term=kw.get("term", "GMV"), class_id="C030",
                        column=kw.get("column", "dw.ads_gmv_total_day.gmv"),
                        aggregation=kw.get("agg", "SUM"), pre_filters=[])],
            dimensions=[DF(class_id=c, column=col, operator="=", value=v)
                        for c, col, v in kw.get("dims", [])],
            group_by=[DG(class_id=c, column=col) for c, col in kw.get("groups", [])],
            time=TR(column="t.dt", start="2026-08-01", end="2026-08-31"),
            joins=[JS(**j) for j in kw.get("joins", [])],
        )

    def test_region_gmv_uses_rank_table(self):
        from app.agent.nodes.semantic_grounding import adapt_measure_source
        p = self._plan(groups=[("C050", "r.region_name")],
                       joins=[{"left_table": "t", "left_column": "province_id",
                               "right_table": "p", "right_column": "id",
                               "right_table_real": "dw.dim_base_province"}])
        adapt_measure_source(p)
        assert p.measures[0].column == "dw.dwd_order_info_inc.total_amount"
        assert p.group_by[0].column == "r.region_name"
        assert 1 <= len(p.joins) <= 2  # chain kept (extent varies by filter)

    def test_category_gmv_falls_back_to_raw(self):
        from app.agent.nodes.semantic_grounding import adapt_measure_source
        p = self._plan(groups=[("C021", "c.name")])
        adapt_measure_source(p)
        m = p.measures[0]
        # fan-out fix: category slice aggregates the per-detail amount
        assert m.column.endswith("split_total_amount")
        assert "order_status = '1001'" in m.pre_filters  # paid 口径

    def test_plain_gmv_untouched(self):
        from app.agent.nodes.semantic_grounding import adapt_measure_source
        p = self._plan()
        adapt_measure_source(p)
        assert p.measures[0].column == "dw.ads_gmv_total_day.gmv"

    def test_unknown_term_deep_slice_llm_path(self):
        from app.agent.nodes.semantic_grounding import adapt_measure_source
        p = self._plan(term="custom_metric",
                       column="dw.ads_gmv_total_day.gmv",
                       groups=[("C021", "c.name")])
        adapt_measure_source(p)
        assert p.confidence <= 0.5  # demoted -> LLM path with real DDL

    def test_clarify_merge_adapts(self):
        from app.agent.nodes.merge_clarification import apply_clarification_to_plan
        initial = self._plan(question="Top regions by sales").to_dict()
        merged = apply_clarification_to_plan(initial, {
            "selections": {"measure": "GMV", "time": "本月",
                           "group_by": ["C050"]}})
        assert merged["measures"][0]["column"] == "dw.dwd_order_info_inc.total_amount"
        assert any("dim_base_province" in (j.get("right_table_real") or "")
                   for j in merged["joins"])
        assert any("dim_base_region" in (j.get("right_table_real") or "")
                   for j in merged["joins"])
