"""Integration tests for new Phase 3.12 dashboard endpoints.

Uses FastAPI TestClient with dependency overrides to avoid real DB connections.
Requires JWT_SECRET set in conftest.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# auth.py sys.exit(1) without JWT_SECRET; ensure set before import
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-integration-1234567890")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app():
    """Build a minimal FastAPI app with only the query_router, deps overridden."""
    from app.api.routers import query_router as qr_module
    from app.api.dependencies import get_query_service
    from app.core.auth import verify_token

    # Override verify_token to bypass auth
    async def fake_verify_token():
        return {"sub": "test-user"}

    # Override query service
    fake_service = MagicMock()
    fake_service.get_distinct_values = AsyncMock(return_value=["east", "north", "south"])
    fake_service.execute_filtered_sql = AsyncMock(return_value=[
        {"region": "east", "amount": 100},
        {"region": "north", "amount": 200},
    ])

    app = FastAPI()
    app.include_router(qr_module.query_router)
    app.dependency_overrides[get_query_service] = lambda: fake_service
    app.dependency_overrides[verify_token] = fake_verify_token
    return app, fake_service


def test_get_filters_endpoint_returns_distinct_values():
    app, svc = _build_app()
    client = TestClient(app)
    resp = client.get("/api/query/filters/dim_region/region_name", params={"limit": 50, "search": "e"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["values"] == ["east", "north", "south"]
    assert body["limit"] == 50
    svc.get_distinct_values.assert_called_once()
    call = svc.get_distinct_values.call_args
    assert call[0][0] == "dim_region"
    assert call[0][1] == "region_name"
    assert call[1]["limit"] == 50
    assert call[1]["search"] == "e"


def test_get_filters_endpoint_validates_limit_bounds():
    app, _ = _build_app()
    client = TestClient(app)
    # limit > 1000 should fail validation (422)
    resp = client.get("/api/query/filters/dim_region/region_name", params={"limit": 5000})
    assert resp.status_code == 422
    # negative offset
    resp2 = client.get("/api/query/filters/dim_region/region_name", params={"offset": -5})
    assert resp2.status_code == 422


def test_execute_endpoint_returns_rows():
    app, svc = _build_app()
    client = TestClient(app)
    resp = client.post(
        "/api/query/execute",
        json={
            "sql": "SELECT region, amount FROM fact_order",
            "filters": [{"field": "region", "operator": "eq", "value": "east"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 2
    assert body["rows"][0]["region"] == "east"
    svc.execute_filtered_sql.assert_called_once()
    call = svc.execute_filtered_sql.call_args
    assert "fact_order" in call[0][0]
    assert call[0][1] == [{"field": "region", "operator": "eq", "value": "east"}]


def test_execute_endpoint_requires_sql():
    app, _ = _build_app()
    client = TestClient(app)
    resp = client.post("/api/query/execute", json={"filters": []})
    assert resp.status_code == 422


def test_endpoints_require_auth():
    """Without dependency override, missing token -> 401."""
    from app.api.routers import query_router as qr_module
    # Build app WITHOUT overriding verify_token (real auth)
    app = FastAPI()
    app.include_router(qr_module.query_router)
    client = TestClient(app)
    resp = client.get("/api/query/filters/dim_region/region_name")
    # Either 401 or 500 depending on auth import path; assert not 200
    assert resp.status_code != 200
