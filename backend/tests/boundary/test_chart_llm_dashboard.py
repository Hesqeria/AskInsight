"""Chart recommendation + multi-LLM + Dashboard boundary tests"""
import pytest
from app.agent.nodes.chart_recommender import _detect_chart_heuristic, _is_numeric


# === Chart recommendation ===
def test_chart_card():
    assert _detect_chart_heuristic([{'total': 100}]) == 'card'

def test_chart_pie():
    data = [{'region': 'A', 'amt': 100}, {'region': 'B', 'amt': 200}]
    assert _detect_chart_heuristic(data) == 'pie'

def test_chart_line_time():
    data = [{'month': 1, 'amt': 100}, {'month': 2, 'amt': 200}, {'month': 3, 'amt': 150}]
    assert _detect_chart_heuristic(data) == 'line'

def test_chart_bar():
    data = [{'region': f'R{i}', 'amt': i*100} for i in range(7)]
    assert _detect_chart_heuristic(data) == 'bar'

def test_chart_empty():
    assert _detect_chart_heuristic([]) == 'table'

def test_chart_mixed_types():
    data = [{'name': 'A', 'count': 10, 'amount': 100.5},
            {'name': 'B', 'count': 20, 'amount': 200.5},
            {'name': 'C', 'count': 30, 'amount': 300.5}]
    chart = _detect_chart_heuristic(data)
    assert chart in ('bar', 'scatter')

def test_is_numeric():
    assert _is_numeric(100) == True
    assert _is_numeric('abc') == False
    assert _is_numeric(None) == False
    assert _is_numeric(3.14) == True


# === Multi-LLM adaptation ===
def test_llm_providers():
    from app.agent.llm_provider import SUPPORTED_PROVIDERS
    assert len(SUPPORTED_PROVIDERS) >= 10
    assert 'deepseek' in SUPPORTED_PROVIDERS
    assert 'openai' in SUPPORTED_PROVIDERS
    assert 'kimi' in SUPPORTED_PROVIDERS

def test_llm_build():
    from app.agent.llm_provider import build_llm
    llm = build_llm(provider='deepseek')
    assert llm is not None
    assert llm.temperature == 0


# === Superset ===
def test_superset_service_exists():
    from app.services.superset_service import create_dashboard_from_queries, get_dashboards
    assert callable(create_dashboard_from_queries)
    assert callable(get_dashboards)


# === Admin Router ===
def test_admin_routes():
    from app.api.routers.admin_router import admin_router
    paths = [r.path for r in admin_router.routes]
    assert '/api/admin/llm/providers' in paths
    assert '/api/admin/llm/switch' in paths
    assert '/api/admin/dashboards' in paths
    assert '/api/admin/chart/recommend' in paths
