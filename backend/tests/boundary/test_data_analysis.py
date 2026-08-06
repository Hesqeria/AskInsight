"""Data analysis capability boundary tests"""
import pytest
from app.services.stats_service import describe, growth_rate, percentage_breakdown, rank_with_gap, correlation
from app.services.export_service import export_csv_bytes, export_json_bytes


def test_stats_describe():
    r = describe([1, 2, 3, 4, 5])
    assert r["count"] == 5
    assert r["mean"] == 3.0


def test_stats_growth_up():
    r = growth_rate(120, 100)
    assert r["growth_rate_pct"] == 20.0
    assert r["direction"] == "up"


def test_stats_growth_down():
    r = growth_rate(80, 100)
    assert r["growth_rate_pct"] == -20.0


def test_stats_breakdown():
    data = [{"cat": "A", "amt": 75}, {"cat": "B", "amt": 25}]
    result = percentage_breakdown(data, "cat", "amt")
    assert result[0]["percentage"] == 75.0


def test_stats_rank():
    data = [{"name": "A", "val": 100}, {"name": "B", "val": 80}]
    result = rank_with_gap(data, "val")
    assert result[0]["rank"] == 1
    assert result[1]["gap_to_top"] == 20.0


def test_stats_correlation():
    data = [{"x": 1, "y": 2}, {"x": 2, "y": 4}, {"x": 3, "y": 6}]
    r = correlation(data, "x", "y")
    assert abs(r["correlation"] - 1.0) < 0.01


def test_export_csv():
    data = [{"a": 1, "b": "hello"}]
    result = export_csv_bytes(data)
    assert b"hello" in result


def test_export_json():
    result = export_json_bytes([{"a": 1}])
    assert b"1" in result


def test_export_excel():
    from app.services.export_service import export_excel
    result = export_excel([{"col1": "v1"}])
    assert len(result) > 0
    assert result[:2] == b"PK"


def test_code_validate_safe():
    from app.agent.nodes.code_executor import validate_code
    ok, msg = validate_code("x = 1 + 2")
    assert ok


def test_code_validate_os():
    from app.agent.nodes.code_executor import validate_code
    ok, msg = validate_code("import os")
    assert not ok


def test_code_validate_subprocess():
    from app.agent.nodes.code_executor import validate_code
    ok, msg = validate_code("import subprocess")
    assert not ok


def test_graph_has_code_executor():
    from app.agent.graph import graph
    nodes = list(graph.get_graph().nodes.keys())
    assert "code_executor" in nodes
