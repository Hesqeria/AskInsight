"""Integration tests for P4-02 metadata router and P6-01 alerts webhook router.

Uses FastAPI TestClient with dependency overrides to avoid real DB connections.
Requires JWT_SECRET set in conftest.
"""
import os

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-integration-1234567890")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import metadata_router as md
from app.api.routers import alerts_router as ar
from app.core.auth import verify_token


def _build_metadata_app():
    from app.core.auth import verify_token as vt

    async def fake_verify_token():
        return {"sub": "test-user", "role": "admin"}

    app = FastAPI()
    app.include_router(md.metadata_router)
    app.dependency_overrides[vt] = fake_verify_token
    return app


def _build_alerts_app():
    app = FastAPI()
    app.include_router(ar.alerts_router)
    return app


def test_metadata_list_tables():
    app = _build_metadata_app()
    client = TestClient(app)
    resp = client.get("/metadata/tables", params={"role": "L1_business"})
    assert resp.status_code == 200
    tables = resp.json()["tables"]
    assert len(tables) >= 2
    assert any(t["name"] == "ods_order" for t in tables)


def test_metadata_get_table_by_id():
    app = _build_metadata_app()
    client = TestClient(app)
    resp = client.get("/metadata/tables/dws_gmv_daily")
    assert resp.status_code == 200
    assert resp.json()["name"] == "dws_gmv_daily"


def test_metadata_get_table_missing():
    app = _build_metadata_app()
    client = TestClient(app)
    resp = client.get("/metadata/tables/nonexistent_xyz")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_metadata_semantic_search():
    app = _build_metadata_app()
    client = TestClient(app)
    resp = client.post("/metadata/columns/search",
                       json={"query": "payment_amount", "top_k": 3})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) >= 1
    assert results[0]["name"] == "payment_amount"


def test_metrics_by_alias():
    app = _build_metadata_app()
    client = TestClient(app)
    resp = client.get("/metrics", params={"alias": "sales"})
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    assert any(m["name"] == "GMV" for m in metrics)


def test_glossary_by_term():
    app = _build_metadata_app()
    client = TestClient(app)
    resp = client.get("/glossary", params={"term": "GMV"})
    assert resp.status_code == 200
    assert resp.json()["term"] == "GMV"


def test_metadata_lineage_endpoint():
    app = _build_metadata_app()
    client = TestClient(app)
    resp = client.get("/metadata/lineage",
                      params={"table": "dws_gmv_daily", "direction": "upstream"})
    assert resp.status_code == 200
    assert "dwd_order_detail" in resp.json()["nodes"]


def test_metadata_relations_endpoint():
    app = _build_metadata_app()
    client = TestClient(app)
    resp = client.get("/metadata/relations", params={"entity": "user"})
    assert resp.status_code == 200
    assert len(resp.json()["relations"]) >= 1


def test_metadata_requires_auth():
    from app.core.auth import verify_token as vt
    app = FastAPI()
    app.include_router(md.metadata_router)
    client = TestClient(app)
    resp = client.get("/metadata/tables")
    assert resp.status_code != 200


def test_prometheus_webhook_ingests():
    app = _build_alerts_app()
    client = TestClient(app)
    resp = client.post("/alerts/webhook/prometheus", json={
        "alerts": [{
            "fingerprint": "f-1",
            "labels": {"alertname": "CPUHigh", "severity": "warning", "host": "h1"},
            "annotations": {},
            "status": "firing",
            "startsAt": "2024-01-01T00:00:00Z",
        }]
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["ingested"] == 1


def test_prometheus_webhook_ingests_many():
    app = _build_alerts_app()
    client = TestClient(app)
    alerts = [{
        "fingerprint": f"f-{i}",
        "labels": {"alertname": "CPUHigh", "severity": "warning", "host": f"h{i}"},
        "annotations": {}, "status": "firing",
        "startsAt": "2024-01-01T00:00:00Z",
    } for i in range(3)]
    resp = client.post("/alerts/webhook/prometheus", json={"alerts": alerts})
    assert resp.json()["ingested"] == 3


def test_airflow_webhook():
    app = _build_alerts_app()
    client = TestClient(app)
    resp = client.post("/alerts/webhook/airflow/task_failed", json={
        "dag_id": "dag_etl_daily", "task_id": "load", "dag_run_id": "run-9",
    })
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert resp.json()["alert_uid"]


def test_custom_webhook():
    app = _build_alerts_app()
    client = TestClient(app)
    resp = client.post("/alerts/webhook/custom", json={
        "title": "custom alert", "severity": "critical",
        "labels": {"service": "dw"}, "annotations": {},
    })
    assert resp.status_code == 200
    assert resp.json()["alert_uid"]


def test_doris_webhook():
    app = _build_alerts_app()
    client = TestClient(app)
    resp = client.post("/alerts/webhook/doris", json={
        "title": "BE down", "severity": "critical", "labels": {"host": "be1"},
    })
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_webhook_health():
    app = _build_alerts_app()
    client = TestClient(app)
    resp = client.get("/alerts/webhook/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
