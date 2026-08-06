"""HTML report generation service: produces a standalone HTML file from query results (includes ECharts charts)."""
import json
import html
from datetime import datetime
from pathlib import Path


def _escape(value) -> str:
    """D-B1: HTML escape to prevent XSS."""
    if value is None:
        return ""
    return html.escape(str(value))


def _detect_chart_type(rows: list, columns: list) -> str:
    """D-F1: automatically choose the chart type."""
    if not rows:
        return "table"
    if len(rows) == 1 and len(columns) == 1:
        return "card"
    numeric_cols = [c for c in columns if _is_numeric(rows[0].get(c))]
    category_cols = [c for c in columns if not _is_numeric(rows[0].get(c))]
    if len(rows) == 1 and len(numeric_cols) == 1:
        return "card"
    if len(category_cols) >= 1 and len(numeric_cols) >= 1:
        if len(rows) <= 6 and len(numeric_cols) == 1:
            return "pie"
        return "bar"
    return "table"


def _is_numeric(val) -> bool:
    if val is None:
        return False
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


def _truncate_rows(rows: list, limit: int = 500) -> list:
    """D-B2: cap the number of rows to prevent the HTML from getting too large."""
    if len(rows) > limit:
        return rows[:limit]
    return rows


def generate_html_report(
    title: str,
    sections: list,
    output_path: str = None,
) -> str:
    """Generate the full HTML report.

    Args:
        title: report title
        sections: [{"question": "Sales by region", "data": [...], "analysis": "..."}]
        output_path: output path (defaults to the reports/ directory)

    Returns:
        HTML file path
    """
    if output_path is None:
        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        output_path = str(report_dir / filename)

    charts_html = []
    for i, section in enumerate(sections):
        question = _escape(section.get("question", ""))
        data = _truncate_rows(section.get("data", []))
        analysis = _escape(section.get("analysis", ""))
        columns = list(data[0].keys()) if data else []
        chart_type = _detect_chart_type(data, columns)

        chart_id = f"chart_{i}"
        chart_js = _build_chart_js(chart_id, chart_type, data, columns)

        section_html = f"""
        <div class="section">
            <h2>{i+1}. {question}</h2>
            <div id="{chart_id}" class="chart-container"></div>
            {f'<div class="analysis"><strong>AI analysis:</strong> {analysis}</div>' if analysis else ''}
        </div>
        """
        charts_html.append((section_html, chart_js))

    body_sections = "\n".join([s for s, _ in charts_html])
    chart_scripts = "\n".join([js for _, js in charts_html])

    full_html = _HTML_TEMPLATE.replace("{{TITLE}}", _escape(title)) \
                               .replace("{{DATE}}", datetime.now().strftime("%Y-%m-%d %H:%M:%S")) \
                               .replace("{{SECTIONS}}", body_sections) \
                               .replace("{{CHART_SCRIPTS}}", chart_scripts)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_html)

    return output_path


def _build_chart_js(chart_id: str, chart_type: str, data: list, columns: list) -> str:
    """Build the ECharts JavaScript."""
    if chart_type == "table" or not data:
        # Render table
        header = "".join([f"<th>{_escape(c)}</th>" for c in columns])
        rows_html = ""
        for row in data[:50]:
            cells = "".join([f"<td>{_escape(row.get(c, ''))}</td>" for c in columns])
            rows_html += f"<tr>{cells}</tr>"
        return f'document.getElementById("{chart_id}").innerHTML = \'<table class="data-table"><thead><tr>{header}</tr></thead><tbody>{rows_html}</tbody></table>\';'

    numeric_cols = [c for c in columns if _is_numeric(data[0].get(c))]
    category_cols = [c for c in columns if not _is_numeric(data[0].get(c))]
    cat_key = category_cols[0] if category_cols else columns[0]

    categories = [str(row.get(cat_key, "")) for row in data]

    if chart_type == "card":
        val = data[0].get(numeric_cols[0], 0) if numeric_cols else 0
        return f'document.getElementById("{chart_id}").innerHTML = \'<div class="card-value">{_escape(val)}</div><div class="card-label">{_escape(numeric_cols[0] if numeric_cols else "")}</div>\';'

    if chart_type == "pie":
        pie_data = [{"name": str(row.get(cat_key, "")), "value": float(row.get(numeric_cols[0], 0) or 0)} for row in data]
        return f'''
        var chart_{chart_id} = echarts.init(document.getElementById("{chart_id}"));
        chart_{chart_id}.setOption({{
            tooltip: {{ trigger: "item" }},
            series: [{{ type: "pie", radius: "60%", data: {json.dumps(pie_data, ensure_ascii=False)} }}]
        }});
        '''

    # bar (default)
    series_list = []
    for nc in numeric_cols:
        values = [float(row.get(nc, 0) or 0) for row in data]
        series_list.append({"name": nc, "type": "bar", "data": values})

    return f'''
    var chart_{chart_id} = echarts.init(document.getElementById("{chart_id}"));
    chart_{chart_id}.setOption({{
        tooltip: {{ trigger: "axis" }},
        legend: {{ data: {json.dumps(numeric_cols)} }},
        xAxis: {{ type: "category", data: {json.dumps(categories, ensure_ascii=False)}, axisLabel: {{ rotate: 30 }} }},
        yAxis: {{ type: "value" }},
        series: {json.dumps(series_list, ensure_ascii=False)}
    }});
    '''


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{TITLE}}</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #f5f7fa; color: #333; padding: 20px; }
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 { font-size: 24px; color: #1a73e8; }
        .header .date { font-size: 13px; color: #999; margin-top: 4px; }
        .section { background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
        .section h2 { font-size: 16px; margin-bottom: 12px; color: #333; border-left: 4px solid #1a73e8; padding-left: 8px; }
        .chart-container { width: 100%; height: 350px; }
        .analysis { margin-top: 12px; padding: 10px; background: #f0f7ff; border-radius: 8px; font-size: 14px; line-height: 1.6; }
        .card-value { font-size: 36px; font-weight: 700; color: #1a73e8; text-align: center; }
        .card-label { font-size: 13px; color: #999; text-align: center; margin-top: 4px; }
        .data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .data-table th { background: #f5f5f5; padding: 8px 12px; text-align: left; border: 1px solid #e0e0e0; }
        .data-table td { padding: 8px 12px; border: 1px solid #e0e0e0; }
        .footer { text-align: center; margin-top: 30px; font-size: 12px; color: #bbb; }
    </style>
</head>
<body>
    <div class="header">
        <h1>{{TITLE}}</h1>
        <div class="date">Generated: {{DATE}}</div>
    </div>
    {{SECTIONS}}
    <div class="footer">Auto-generated by the NL2SQL decision agent</div>
    <script>{{CHART_SCRIPTS}}</script>
</body>
</html>
"""
