"""Standalone-subject measures (cart/favor/good_rate) grounding."""
import datetime
from app.agent.nodes.semantic_grounding import build_plan
from app.ontology.plan import render_sql_from_plan


def _plan(q, kws):
    return build_plan(question=q, keywords=kws, today=datetime.date(2026, 8, 7))


def test_cart_item_count_grounds():
    p = _plan("购物车加购件数", ["购物车", "加购件数"])
    assert p.measures and p.measures[0].business_term == "cart_item_count"
    assert p.measures[0].aggregation == "SUM"
    assert "dwd_cart_info_inc" in p.measures[0].column
    sql = render_sql_from_plan(p)
    assert "SUM(t.sku_num)" in sql and "t.dt BETWEEN" in sql


def test_good_rate_ratio_render():
    p = _plan("好评率", ["好评率"])
    assert p.measures and p.measures[0].business_term == "good_rate"
    sql = render_sql_from_plan(p)
    assert "SUM(t.good_comment_count)" in sql
    assert "NULLIF(SUM(t.comment_count), 0)" in sql
    assert "* 100" in sql


def test_favor_count_not_caught_by_topic_guard():
    p = _plan("近7天收藏数", ["收藏数", "近7天"])
    assert p.measures and p.measures[0].business_term == "favor_count"
    sql = render_sql_from_plan(p)
    assert "dwd_favor_info_inc" in sql


def test_topic_guard_still_blocks_generic():
    # 收藏+GMV generic still drops to clarify
    p = _plan("收藏的GMV是多少", ["收藏", "GMV"])
    assert not p.measures
