"""P6-01: Multi-source alert webhook endpoints."""
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.alerts.deduper import get_deduper
from app.alerts.ingestor import AlertIngestor, RawAlert, AlertSource

alerts_router = APIRouter()


class CustomAlertRequest(BaseModel):
    alert_id: Optional[str] = None
    title: str
    severity: str = "warning"
    labels: dict = {}
    annotations: dict = {}
    starts_at: Optional[str] = None
    status: str = "firing"


async def _get_ingestor() -> AlertIngestor:
    ing = AlertIngestor(deduper=get_deduper())
    return ing


@alerts_router.post("/alerts/webhook/prometheus")
async def prometheus_webhook(payload: dict):
    ing = await _get_ingestor()
    count = await ing.ingest_prometheus(payload)
    return {"code": 0, "ingested": count}


@alerts_router.post("/alerts/webhook/airflow/{event}")
async def airflow_webhook(event: str, payload: dict):
    ing = await _get_ingestor()
    uid = await ing.ingest_airflow_event(payload)
    return {"code": 0, "alert_uid": uid, "event": event}


@alerts_router.post("/alerts/webhook/custom")
async def custom_webhook(req: CustomAlertRequest):
    ing = await _get_ingestor()
    alert = RawAlert(
        alert_id=req.alert_id or "custom",
        source=AlertSource.CUSTOM,
        title=req.title,
        severity=req.severity,
        labels=req.labels,
        annotations=req.annotations,
        starts_at=req.starts_at or "",
        status=req.status,
    )
    uid = await ing.ingest(alert)
    return {"code": 0, "alert_uid": uid}


@alerts_router.post("/alerts/webhook/doris")
async def doris_webhook(payload: dict):
    ing = await _get_ingestor()
    alert = RawAlert(
        alert_id=payload.get("alert_id", "doris"),
        source=AlertSource.DORIS,
        title=payload.get("title", "Doris alert"),
        severity=payload.get("severity", "warning"),
        labels=payload.get("labels", {}),
        annotations=payload.get("annotations", {}),
        starts_at=payload.get("starts_at", ""),
        status=payload.get("status", "firing"),
    )
    uid = await ing.ingest(alert)
    return {"code": 0, "alert_uid": uid}


@alerts_router.get("/alerts/webhook/health")
async def webhook_health(request: Request):
    return {"status": "ok"}
