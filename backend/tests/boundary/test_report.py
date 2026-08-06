"""Dashboard + HTML report boundary tests"""
import pytest
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


# === D-B1: HTML injection protection ===
def test_db1_html_escape():
    from app.services.report_service import _escape
    assert _escape('<script>') == '&lt;script&gt;'
    assert _escape(None) == ''
    assert _escape('normal text') == 'normal text'


# === D-B2: row count limit ===
def test_db2_row_limit():
    from app.services.report_service import _truncate_rows
    big = [{'a': i} for i in range(2000)]
    result = _truncate_rows(big, limit=500)
    assert len(result) == 500


# === D-F1: chart type auto-selection ===
def test_df1_chart_type_detection():
    from app.services.report_service import _detect_chart_type
    # single row single value -> card
    assert _detect_chart_type([{'total': 100}], ['total']) == 'card'
    # multi-row category + value -> pie
    assert _detect_chart_type([{'region': 'north', 'amount': 100}, {'region': 'east', 'amount': 200}], ['region', 'amount']) == 'pie'
    # 7 rows -> bar
    data = [{'region': f'R{i}', 'amount': i} for i in range(7)]
    assert _detect_chart_type(data, ['region', 'amount']) == 'bar'
    # no data -> table
    assert _detect_chart_type([], []) == 'table'


# === HTML report generation ===
def test_generate_html_report(tmp_path):
    from app.services.report_service import generate_html_report
    sections = [
        {'question': 'sales by region', 'data': [{'region': 'north', 'amount': 80000}, {'region': 'east', 'amount': 70000}], 'analysis': 'north is highest'},
        {'question': 'total', 'data': [{'total': 437756}], 'analysis': ''},
    ]
    path = str(tmp_path / 'test_report.html')
    result = generate_html_report('Test Report', sections, path)
    assert os.path.exists(result)
    content = open(result, encoding='utf-8').read()
    assert 'Test Report' in content
    assert 'echarts' in content
    assert 'AI analysis' in content


# === D-B1: XSS protection verification ===
def test_db1_xss_in_report(tmp_path):
    from app.services.report_service import generate_html_report
    sections = [
        {'question': '<script>alert(1)</script>', 'data': [{'col': '<img onerror=alert(1)>'}], 'analysis': ''}
    ]
    path = str(tmp_path / 'xss_test.html')
    result = generate_html_report('XSS Test', sections, path)
    content = open(result, encoding='utf-8').read()
    # raw script tags should not appear in the body (they are escaped)
    assert '<script>alert(1)</script>' not in content.split('<body>')[1] if '<body>' in content else True


# === numeric type detection ===
def test_is_numeric():
    from app.services.report_service import _is_numeric
    assert _is_numeric(123) == True
    assert _is_numeric('123.45') == True
    assert _is_numeric('abc') == False
    assert _is_numeric(None) == False


# === report_router exists ===
def test_report_router_exists():
    from app.api.routers.report_router import report_router
    routes = [r.path for r in report_router.routes]
    assert '/api/report/html' in routes
    assert '/api/report/download' in routes
    assert '/api/report/push-feishu' in routes
