"""P10: slice-pattern grouping + fan-out-safe category GMV."""
from datetime import date

from app.agent.nodes.semantic_grounding import build_plan


def test_region_slice_uses_rank_table():
    plan = build_plan("各区域的GMV排行", today=date(2026, 9, 9))
    assert any(g.class_id == "C050" for g in plan.group_by)
    # rank table is dead data -> region GMV re-sources to the dwd chain
    assert plan.measures[0].column.startswith("dw.dwd_order_info_inc.")
    # region_name lives on the rank table -> no province/region joins
    assert any("dim_base_province" in j.right_table_real for j in plan.joins)


def test_region_category_combo_uses_detail_amount():
    plan = build_plan("各区域各品类的GMV", today=date(2026, 9, 9))
    assert plan.measures[0].column.endswith("split_total_amount")
    tables = {j.right_table_real for j in plan.joins}
    assert "dw.dwd_order_detail_inc" in tables
    assert "dw.dim_sku_info" in tables
    assert "dw.dim_base_category3" in tables
    assert "dw.dim_base_province" in tables
    # fact re-sourced off the pre-agg table
    assert not plan.measures[0].column.startswith("dw.ads_gmv_total_day.")
    assert any("1001" in pf or "PAID" in pf.upper()
               for m in plan.measures for pf in m.pre_filters)


def test_category_slice_groups_by_category():
    plan = build_plan("各品类销售额分布", today=date(2026, 9, 9))
    assert any(g.class_id == "C021" for g in plan.group_by)
    assert plan.measures[0].column.endswith("split_total_amount")
