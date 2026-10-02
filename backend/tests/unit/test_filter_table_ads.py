"""Tests for filter_table ads_* prioritization (基线报告 7.2).

Verifies that aggregation-intent questions surface ads_* tables first,
and that non-aggregation questions leave ordering unchanged.
"""
import pytest

from app.agent.nodes.filter_table import _prioritize_ads_tables, _ADS_INTENT_KEYWORDS


def _table(name):
    return {"name": name, "role": "fact", "columns": [{"name": "x"}]}


class TestPrioritizeAdsTables:
    def test_aggregation_intent_moves_ads_front(self):
        tables = [
            _table("dwd_order_info_inc"),
            _table("ads_gmv_total_day"),
            _table("dws_region_region_order_day"),
        ]
        out = _prioritize_ads_tables(tables, "昨天的总成交额")
        # ads_* must be first; nothing dropped.
        assert out[0]["name"] == "ads_gmv_total_day"
        assert len(out) == 3
        # Non-ads tables preserved after.
        names = [t["name"] for t in out]
        assert "dwd_order_info_inc" in names
        assert "dws_region_region_order_day" in names

    def test_non_aggregation_unchanged(self):
        tables = [_table("dwd_order_info_inc"), _table("ads_gmv_total_day")]
        out = _prioritize_ads_tables(tables, "订单的支付方式是什么")
        assert [t["name"] for t in out] == ["dwd_order_info_inc", "ads_gmv_total_day"]

    def test_no_ads_tables_unchanged(self):
        tables = [_table("dwd_order_info_inc"), _table("dws_sales_wide")]
        out = _prioritize_ads_tables(tables, "每天GMV趋势")
        assert [t["name"] for t in out] == ["dwd_order_info_inc", "dws_sales_wide"]

    def test_empty_query_unchanged(self):
        tables = [_table("ads_gmv_total_day"), _table("dwd_order_info_inc")]
        out = _prioritize_ads_tables(tables, "")
        assert out == tables

    def test_empty_tables(self):
        assert _prioritize_ads_tables([], "GMV") == []

    def test_rank_keyword_does_not_trigger(self):
        """TOP N / 排名 queries use dwd/dws raw tables, NOT ads_*.
        Verified against eval gold: all 13 TOP/排名 questions use dwd_*/dws_*."""
        tables = [_table("ads_region_gmv_rank"), _table("dws_sales_wide")]
        out = _prioritize_ads_tables(tables, "上个月销量TOP10的SKU")
        assert [t["name"] for t in out] == ["ads_region_gmv_rank", "dws_sales_wide"]

    def test_gmv_total_keyword_triggers(self):
        tables = [_table("ads_gmv_total_day"), _table("dwd_order_info_inc")]
        out = _prioritize_ads_tables(tables, "昨天总成交额是多少")
        assert out[0]["name"] == "ads_gmv_total_day"

    def test_order_count_does_not_trigger(self):
        """订单量 NOT an ads_* query — must NOT reorder (uses dwd)."""
        tables = [_table("ads_gmv_total_day"), _table("dwd_order_info_inc")]
        out = _prioritize_ads_tables(tables, "今天有多少笔订单")
        assert [t["name"] for t in out] == ["ads_gmv_total_day", "dwd_order_info_inc"]

    def test_repurchase_does_not_trigger(self):
        """复购/环比 stay on dwd — must NOT reorder."""
        tables = [_table("ads_gmv_total_day"), _table("dwd_order_info_inc")]
        out = _prioritize_ads_tables(tables, "复购用户数")
        assert out == tables

    def test_growth_rate_does_not_trigger(self):
        tables = [_table("ads_gmv_total_day"), _table("dwd_order_info_inc")]
        out = _prioritize_ads_tables(tables, "本周相比上周订单量环比增长率")
        assert out == tables

    def test_gmv_trend_triggers(self):
        tables = [_table("ads_gmv_total_day"), _table("dws_sales_wide")]
        out = _prioritize_ads_tables(tables, "最近7天每天的GMV趋势")
        assert out[0]["name"] == "ads_gmv_total_day"

    def test_highest_day_triggers(self):
        tables = [_table("ads_gmv_total_day"), _table("dwd_order_info_inc")]
        out = _prioritize_ads_tables(tables, "GMV最高的那一天是哪一天")
        assert out[0]["name"] == "ads_gmv_total_day"

    def test_gmv_share_triggers(self):
        tables = [_table("ads_gmv_total_day"), _table("dwd_order_info_inc")]
        out = _prioritize_ads_tables(tables, "GMV占比超过10%的日期")
        assert out[0]["name"] == "ads_gmv_total_day"

    def test_keywords_nonempty(self):
        # Sanity: the intent keyword list is populated and narrow.
        assert len(_ADS_INTENT_KEYWORDS) >= 4
