"""Integration tests for P1-08 orchestrator router."""
import os

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-integration-1234567890")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.orchestrator_router import orchestrator_router
from app.core.auth import verify_token


def _build_app():
    async def fake_verify_token():
        return {"sub": "test-user", "role": "admin"}

    app = FastAPI()
    app.include_router(orchestrator_router)
    app.dependency_overrides[verify_token] = fake_verify_token
    return app


def test_dispatch_returns_plan():
    app = _build_app()
    client = TestClient(app)
    resp = client.post("/orchestrator/dispatch",
                       json={"query": "昨日GMV", "intent": "data_query", "metric": "GMV"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["primary_agent"] == "sql_agent"
    assert body["mode"] == "single"


def test_execute_data_query():
    app = _build_app()
    client = TestClient(app)
    resp = client.post("/orchestrator/execute",
                       json={"query": "GMV", "intent": "data_query", "metric": "GMV"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["ok"] is True
    assert "sql" in body["result"]["data"]


def test_execute_etl_sequential():
    app = _build_app()
    client = TestClient(app)
    resp = client.post("/orchestrator/execute",
                       json={"query": "build etl", "intent": "etl_request", "text": "daily gmv"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["ok"] is True
    assert "model" in body["result"]["data"]


def test_execute_quality_check():
    app = _build_app()
    client = TestClient(app)
    resp = client.post("/orchestrator/execute",
                       json={"query": "check quality", "intent": "quality_check", "table": "dws_gmv_daily"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["ok"] is True
    assert body["result"]["data"]["quality"] == "normal"


def test_list_agents():
    app = _build_app()
    client = TestClient(app)
    resp = client.get("/orchestrator/agents")
    assert resp.status_code == 200
    agents = resp.json()["agents"]
    assert len(agents) == 6
    names = {a["name"] for a in agents}
    assert {"sql_agent", "governance_agent", "etl_agent",
            "alert_root_cause_agent", "metric_agent", "intent_agent"} <= names


def test_agent_health():
    app = _build_app()
    client = TestClient(app)
    resp = client.get("/orchestrator/health")
    assert resp.status_code == 200
    health = resp.json()["health"]
    assert "sql_agent" in health


def test_orchestrator_requires_auth():
    app = FastAPI()
    app.include_router(orchestrator_router)
    client = TestClient(app)
    resp = client.get("/orchestrator/agents")
    assert resp.status_code != 200
