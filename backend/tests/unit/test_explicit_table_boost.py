"""SQLBot #1376: an explicitly named table must top the candidates."""
from app.agent.nodes.filter_table import _explicit_table_boost


def _mk(names):
    return [{"name": n, "columns": [{"name": "dt"}]} for n in names]


def test_chinese_alias_boosted_to_front():
    tables = _mk(["ads_gmv_total_day", "dim_date", "dwd_order_detail_inc"])
    out = _explicit_table_boost(tables, "看下订单明细表的金额")
    assert out[0]["name"] == "dwd_order_detail_inc"


def test_verbatim_table_name_boosted():
    tables = _mk(["ads_region_gmv_rank", "ads_gmv_total_day"])
    out = _explicit_table_boost(tables, "ads_gmv_total_day 昨天的GMV")
    assert out[0]["name"] == "ads_gmv_total_day"


def test_no_mention_keeps_order():
    tables = _mk(["ads_gmv_total_day", "dim_date"])
    out = _explicit_table_boost(tables, "昨天GMV")
    assert out[0]["name"] == "ads_gmv_total_day"
