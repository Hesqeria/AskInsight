"""Lineage + anomaly + drill-down boundary tests"""
import pytest
from unittest.mock import MagicMock, AsyncMock


# === Lineage extraction ===
def test_lineage_basic_select():
    from app.agent.nodes.extract_lineage import extract_lineage_from_sql
    sql = 'SELECT SUM(f.order_amount) AS total FROM fact_order f LIMIT 1000'
    lineage = extract_lineage_from_sql(sql)
    assert len(lineage) > 0
    assert any(l['source_table'] == 'fact_order' for l in lineage)


def test_lineage_join():
    from app.agent.nodes.extract_lineage import extract_lineage_from_sql
    sql = 'SELECT r.region_name AS region FROM fact_order f JOIN dim_region r ON f.region_id = r.region_id'
    lineage = extract_lineage_from_sql(sql)
    assert any(l['source_column'] == 'region_name' for l in lineage)


def test_lineage_non_select():
    from app.agent.nodes.extract_lineage import extract_lineage_from_sql
    assert extract_lineage_from_sql("SELECT 'msg' AS message") == []


def test_lineage_chinese_alias():
    from app.agent.nodes.extract_lineage import extract_lineage_from_sql, _is_alias
    assert _is_alias('\u9500\u552e\u603b\u989d') == True
    assert _is_alias('order_amount') == False
    assert _is_alias('FROM') == True


# === Anomaly detection ===
def test_anomaly_extract_numeric():
    from app.agent.nodes.anomaly_detection import _extract_numeric
    data = [{'region': 'north', 'amount': 100.0}, {'region': 'east', 'amount': 200.0}]
    nums = _extract_numeric(data)
    assert len(nums) == 2
    assert nums[0] == ('amount', 100.0)


def test_anomaly_extract_numeric_empty():
    from app.agent.nodes.anomaly_detection import _extract_numeric
    assert _extract_numeric([]) == []
    assert _extract_numeric([{'msg': 'hello'}]) == []


# === Drill-down attribution ===
def test_drill_attribution():
    from app.agent.nodes.drill_down_analysis import _compute_attribution
    dws_region = [
        {'dimension_value': 'south', 'total_amount': 113087.0},
        {'dimension_value': 'east', 'total_amount': 70293.0},
    ]
    result = _compute_attribution(dws_region, [])
    assert result['top_factor'] == 'region=south'
    assert result['contribution_pct'] > 50


def test_drill_lineage_map():
    from app.agent.nodes.drill_down_analysis import LINEAGE_MAP
    assert 'dws_region_sales_daily' in LINEAGE_MAP
    assert LINEAGE_MAP['dws_region_sales_daily']['total_amount']['source'] == 'fact_order.order_amount'


# === Graph integration ===
def test_graph_has_new_nodes():
    from app.agent.graph import graph
    nodes = list(graph.get_graph().nodes.keys())
    assert 'extract_lineage' in nodes
    assert 'anomaly_detection' in nodes
    assert 'drill_down_analysis' in nodes
