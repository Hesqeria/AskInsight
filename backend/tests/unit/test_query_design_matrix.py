"""Query-design upgrades: multi-measure, extremum-count, HAVING,
exclusion, gender slice, distribution disambiguation."""
import datetime
import pytest
from app.agent.nodes.semantic_grounding import build_plan, match_business_terms
from app.ontology.plan import render_sql_from_plan

TODAY = datetime.date(2026, 8, 7)
CHAO, GUO, WAN = chr(0x8D85), chr(0x8FC7), chr(0x4E07)
BJ = chr(0x5317) + chr(0x4EAC)


def _p(q, dv=None):
    return build_plan(question=q, keywords=[], today=TODAY,
                      matched_dimension_values=dv or [])


def test_multi_measure_both_selected():
    p = _p("近7天GMV和订单数")
    assert [m.business_term for m in p.measures] == ["GMV", "order_count"]
    sql = render_sql_from_plan(p)
    assert 'SUM(t.gmv) AS "GMV"' in sql and 'SUM(t.order_count)' in sql


def test_match_terms_longest_first():
    terms = match_business_terms("平均订单金额和GMV")
    assert terms[0] == "avg_order_amount"


def test_extremum_with_count_limits():
    assert _p("GMV最高的3个省份").limit == 3
    assert _p("GMV最低的5个省份").limit == 5
    assert _p("GMV最高的省份").limit == 1


def test_having_threshold_scaled():
    p = _p("GMV" + CHAO + GUO + "10" + WAN + "的省份有哪些")
    assert p.having == ["SUM(t.gmv) > 100000"]
    sql = render_sql_from_plan(p)
    assert "HAVING SUM(t.gmv) > 100000" in sql


def test_having_inclusive_words():
    p = _p("GMV至少5" + WAN + "的省份")
    assert p.having == ["SUM(t.gmv) >= 50000"]


def test_exclusion_negates_filter():
    dv = [{"table_name": "dw.dim_base_province", "value": BJ}]
    p = _p("除北京外的省份GMV", dv=dv)
    neg = [d for d in p.dimensions if d.value == BJ]
    assert neg and neg[0].operator == "!="


def test_gender_slice_groups_and_resources():
    p = _p("用户数按性别分布")
    assert any(g.class_id == "C012" for g in p.group_by)
    assert not any(g.column == "t.dt" for g in p.group_by)
    sql = render_sql_from_plan(p)
    assert "u.gender" in sql and "dwd_order_info_inc" in sql


def test_trend_still_groups_dt():
    p = _p("近7天每天GMV趋势")
    assert any(g.column == "t.dt" for g in p.group_by)


def test_bare_distribution_still_dt():
    p = _p("近30天GMV分布")
    assert any(g.column == "t.dt" for g in p.group_by)
