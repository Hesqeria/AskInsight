"""Superset Dashboard auto-creation with token expiry + concurrency lock."""
import asyncio
import time
import os
import httpx
from datetime import datetime
from app.core.log import logger

SUPERSET_URL = os.getenv("SUPERSET_URL", "http://127.0.0.1:8088")
SUPERSET_USER = os.getenv("SUPERSET_USER", "admin")
SUPERSET_PASSWORD = os.getenv("SUPERSET_PASSWORD", "admin")

_token: str | None = None
_token_expiry: float = 0
_login_lock = asyncio.Lock()
TOKEN_TTL = 3600  # 1 hour


async def _login() -> str:
    """Log in to Superset with token caching + expiry + concurrency lock."""
    global _token, _token_expiry
    if _token and time.time() < _token_expiry:
        return _token
    async with _login_lock:
        # Double-check after acquiring lock
        if _token and time.time() < _token_expiry:
            return _token
        try:
            r = await asyncio.to_thread(
                httpx.post,
                f"{SUPERSET_URL}/api/v1/security/login",
                json={"username": SUPERSET_USER, "password": SUPERSET_PASSWORD, "provider": "db"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            _token = data["access_token"]
            _token_expiry = time.time() + TOKEN_TTL
            return _token
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.error(f"Superset login failed: {e}")
            raise RuntimeError(f"Superset authentication failed: {e}")


async def _headers() -> dict:
    token = await _login()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def create_dashboard_from_queries(title: str, queries: list, chart_types: list = None) -> dict:
    """Create a Dashboard from multiple query sets."""
    result = {"charts": [], "dashboard_url": ""}
    try:
        headers = await _headers()
        r = await asyncio.to_thread(
            httpx.post,
            f"{SUPERSET_URL}/api/v1/dashboard/",
            headers=headers,
            json={"dashboard_title": title[:200], "published": True},
            timeout=10,
        )
        if r.status_code not in (200, 201):
            logger.warning(f"Dashboard creation failed: {r.text[:100]}")
            return result
        dashboard_id = r.json().get("id")
        result["dashboard_id"] = dashboard_id

        for i, q in enumerate(queries):
            chart_type = (chart_types[i] if chart_types and i < len(chart_types) else "table")
            chart_name = str(q.get("name", f"Chart {i+1}"))[:100]
            sql = str(q.get("sql", ""))[:5000]
            r2 = await asyncio.to_thread(
                httpx.post,
                f"{SUPERSET_URL}/api/v1/dataset/",
                headers=headers,
                json={
                    "database": 1,
                    "table_name": f"query_{int(datetime.now().timestamp())}_{i}",
                    "schema": "data_agent",
                    "sql": sql,
                },
                timeout=10,
            )
            if r2.status_code in (200, 201):
                dataset_id = r2.json().get("id")
                result["charts"].append({"name": chart_name, "type": chart_type, "dataset_id": dataset_id})

        result["dashboard_url"] = f"{SUPERSET_URL}/superset/dashboard/{dashboard_id}/"
        logger.info(f"Dashboard created: {title} ({len(result['charts'])} charts)")
    except Exception as e:
        logger.error(f"Dashboard creation failed: {e}")
    return result


async def get_dashboards() -> list:
    """Get the list of all dashboards."""
    try:
        headers = await _headers()
        r = await asyncio.to_thread(
            httpx.get,
            f"{SUPERSET_URL}/api/v1/dashboard/",
            headers=headers,
            params={"page_size": 20},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception as e:
        logger.warning(f"Failed to fetch dashboard list: {e}")
    return []
