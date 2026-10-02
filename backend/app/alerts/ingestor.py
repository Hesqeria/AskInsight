"""P6-01: Multi-source alert ingestion - unified RawAlert schema."""
import uuid
from collections import deque


class AlertSource:
    PROMETHEUS = "prometheus"
    GRAFANA = "grafana"
    AIRFLOW = "airflow"
    DORIS = "doris"
    CUSTOM = "custom"


class RawAlert:
    def __init__(self, alert_id, source, title, severity, labels=None,
                 annotations=None, starts_at="", ends_at=None, status="firing",
                 received_at=""):
        self.alert_id = alert_id
        self.source = source
        self.title = title
        self.severity = severity
        self.labels = labels or {}
        self.annotations = annotations or {}
        self.starts_at = starts_at
        self.ends_at = ends_at
        self.status = status
        self.received_at = received_at or starts_at

    def to_dict(self):
        return {"alert_id": self.alert_id, "source": self.source, "title": self.title,
                "severity": self.severity, "labels": self.labels,
                "annotations": self.annotations, "starts_at": self.starts_at,
                "ends_at": self.ends_at, "status": self.status,
                "received_at": self.received_at}


class AlertIngestor:
    # Default cap on the in-memory recent-ingestion buffer. Production
    # deployments back this with Doris; the in-process buffer exists
    # only for recent-fanout reads and must not grow unbounded.
    DEFAULT_MAX_INGESTED = 1000

    def __init__(self, doris=None, kafka=None, deduper=None,
                 max_ingested: int = DEFAULT_MAX_INGESTED):
        self.doris = doris
        self.kafka = kafka
        self.deduper = deduper
        self._max_ingested = max(1, int(max_ingested))
        self._ingested = deque(maxlen=self._max_ingested)

    async def ingest(self, alert: RawAlert) -> str:
        alert_uid = uuid.uuid4().hex
        # deque(maxlen=...) auto-evicts the oldest entry when full, so
        # this no longer leaks memory in long-running processes.
        self._ingested.append({**alert.to_dict(), "alert_uid": alert_uid})
        if self.doris is not None:
            try:
                self.doris.execute(
                    "INSERT INTO data_agent.alerts_raw (alert_uid, source, title, severity, alert_id, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    params=(alert_uid, alert.source, alert.title, alert.severity,
                            alert.alert_id, alert.status))
            except Exception:
                pass
        if self.deduper is not None:
            try:
                await self.deduper.process(alert)
            except Exception:
                pass
        return alert_uid

    async def ingest_prometheus(self, payload: dict) -> int:
        count = 0
        for a in payload.get("alerts", []):
            alert = RawAlert(
                alert_id=a.get("fingerprint") or str(uuid.uuid4()),
                source=AlertSource.PROMETHEUS,
                title=a.get("labels", {}).get("alertname", "unknown"),
                severity=a.get("labels", {}).get("severity", "warning"),
                labels=a.get("labels", {}),
                annotations=a.get("annotations", {}),
                starts_at=a.get("startsAt", ""),
                ends_at=a.get("endsAt"),
                status=a.get("status", "firing"),
            )
            await self.ingest(alert)
            count += 1
        return count

    async def ingest_airflow_event(self, event: dict) -> str:
        alert = RawAlert(
            alert_id=event.get("dag_run_id", str(uuid.uuid4())),
            source=AlertSource.AIRFLOW,
            title="Airflow task failed: " + event.get("dag_id", "unknown"),
            severity="warning",
            labels={"dag_id": event.get("dag_id", ""),
                    "task_id": event.get("task_id", ""), "job": "airflow"},
            annotations={"run_id": event.get("dag_run_id", "")},
        )
        return await self.ingest(alert)

    def get_ingested(self):
        return list(self._ingested)


_ingestor = None


def get_ingestor() -> AlertIngestor:
    global _ingestor
    if _ingestor is None:
        _ingestor = AlertIngestor()
    return _ingestor
