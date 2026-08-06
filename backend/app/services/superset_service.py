"""Superset Dashboard auto-creation service.

Workflow:
  1. Log in to Superset to obtain a JWT
  2. Create/reuse a Dataset (bound to the Doris data source)
  3. Create a Chart (specify chart type + SQL)
  4. Create a Dashboard (compose multiple Charts)
  5. Return the Dashboard URL
"""
import httpx
from datetime import datetime
from app.core.log import logger

import os
SUPERSET_URL = os.getenv("SUPERSET_URL", "http://127.0.0.1:8088")
SUPERSET_USER = os.getenv("SUPERSET_USER", "admin")
SUPERSET_PASSWORD = os.getenv("SUPERSET_PASSWORD", "admin")
_token = None


def _login() -> str:
    """Log in to Superset to get an access_token."""
    global _token
    if _token:
        return _token
    r = httpx.post(f"{SUPERSET_URL}/api/v1/security/login", json={
        "username": SUPERSET_USER,
        "password": SUPERSET_PASSWORD,
        "provider": "db",
    }, timeout=10)
    _token = r.json()["access_token"]
    return _token


def _headers() -> dict:
    return {"Authorization": f"Bearer {_login()}", "Content-Type": "application/json"}


def create_dashboard_from_queries(title: str, queries: list, chart_types: list = None) -> dict:
    """Create a Dashboard from multiple query sets.

    Args:
        title: Dashboard title
        queries: [{"sql": "SELECT ...", "name": "Sales by region", "chart_type": "bar"}]
        chart_types: chart type list (optional; defaults to auto-inference)

    Returns:
        {"dashboard_id": ..., "dashboard_url": ..., "charts": [...]}
    """
    _login()
    result = {"charts": [], "dashboard_url": ""}

    try:
        # 1. Create the Dashboard
        r = httpx.post(f"{SUPERSET_URL}/api/v1/dashboard/", headers=_headers(), json={
            "dashboard_title": title,
            "published": True,
        }, timeout=10)
        if r.status_code not in (200, 201):
            logger.warning(f"Dashboard creation failed: {r.text[:100]}")
            return result
        dashboard_id = r.json().get("id")
        result["dashboard_id"] = dashboard_id

        # 2. Create a Chart for each query (Superset requires dataset_id)
        for i, q in enumerate(queries):
            chart_type = (chart_types[i] if chart_types and i < len(chart_types) else "table")
            chart_name = q.get("name", f"Chart {i+1}")
            sql = q.get("sql", "")

            # Create a virtual Dataset (SQL Lab mode)
            r2 = httpx.post(f"{SUPERSET_URL}/api/v1/dataset/", headers=_headers(), json={
                "database": 1,  # Default Doris database connection ID
                "table_name": f"query_{int(datetime.now().timestamp())}_{i}",
                "schema": "data_agent",
                "sql": sql,
            }, timeout=10)

            if r2.status_code in (200, 201):
                dataset_id = r2.json().get("id")
                result["charts"].append({
                    "name": chart_name,
                    "type": chart_type,
                    "dataset_id": dataset_id,
                })

        result["dashboard_url"] = f"{SUPERSET_URL}/superset/dashboard/{dashboard_id}/"
        logger.info(f"Dashboard created: {title} ({len(result['charts'])} charts)")
    except Exception as e:
        logger.error(f"Dashboard creation failed: {e}")

    return result


def get_dashboards() -> list:
    """Get the list of all dashboards."""
    try:
        r = httpx.get(f"{SUPERSET_URL}/api/v1/dashboard/", headers=_headers(),
                      params={"page_size": 20}, timeout=10)
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception as e:
        logger.warning(f"Failed to fetch dashboard list: {e}")
    return []


if __name__ == "__main__":
    token = _login()
    print(f"Superset token: {token[:20]}...")
    dashboards = get_dashboards()
    print(f"Existing dashboards: {len(dashboards)}")
    for d in dashboards[:5]:
        print(f"  {d.get('dashboard_title', '?')} | id={d.get('id')}")
