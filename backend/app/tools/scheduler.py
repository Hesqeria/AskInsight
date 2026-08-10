"""P4-04: Airflow scheduler wrapper - create/trigger/status/pause DAGs."""
import json
import httpx


class DAGDefinition:
    def __init__(self, dag_id, schedule, tasks, owner="admin", description=""):
        self.dag_id = dag_id
        self.schedule = schedule
        self.tasks = tasks
        self.owner = owner
        self.description = description

    def to_dict(self):
        return {"dag_id": self.dag_id, "schedule": self.schedule,
                "tasks": self.tasks, "owner": self.owner,
                "description": self.description}


class DAGStatus:
    def __init__(self, dag_id, run_id, state, start_date=None, end_date=None):
        self.dag_id = dag_id
        self.run_id = run_id
        self.state = state
        self.start_date = start_date
        self.end_date = end_date

    def to_dict(self):
        return {"dag_id": self.dag_id, "run_id": self.run_id, "state": self.state,
                "start_date": self.start_date, "end_date": self.end_date}


class DAGInfo:
    def __init__(self, dag_id, is_paused, schedule, description=""):
        self.dag_id = dag_id
        self.is_paused = is_paused
        self.schedule = schedule
        self.description = description


class AirflowScheduler:
    """Wrap Airflow REST API."""

    def __init__(self, base_url=None, token=None, client=None):
        self.base_url = (base_url or "http://127.0.0.1:8080/api/v2").rstrip("/")
        self.token = token or ""
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self._client = client

    async def trigger_dag(self, dag_id, conf=None) -> str:
        client = self._client or httpx.AsyncClient()
        try:
            r = await client.post(
                f"{self.base_url}/dags/{dag_id}/dagRuns",
                json={"conf": conf or {}}, headers=self.headers)
            r.raise_for_status()
            return r.json().get("dag_run_id", f"manual_{dag_id}")
        finally:
            if self._client is None:
                await client.aclose()

    async def get_dag_status(self, dag_id, run_id) -> DAGStatus:
        client = self._client or httpx.AsyncClient()
        try:
            r = await client.get(
                f"{self.base_url}/dags/{dag_id}/dagRuns/{run_id}",
                headers=self.headers)
            if r.status_code == 404:
                return DAGStatus(dag_id, run_id, "not_found")
            r.raise_for_status()
            data = r.json()
            return DAGStatus(dag_id, run_id, data.get("state", "unknown"),
                             data.get("start_date"), data.get("end_date"))
        finally:
            if self._client is None:
                await client.aclose()

    async def list_dags(self, prefix=None) -> list:
        client = self._client or httpx.AsyncClient()
        try:
            r = await client.get(f"{self.base_url}/dags", headers=self.headers)
            r.raise_for_status()
            items = r.json().get("dags", [])
            if prefix:
                items = [d for d in items if d.get("dag_id", "").startswith(prefix)]
            return [DAGInfo(d.get("dag_id"), d.get("is_paused", False),
                            d.get("schedule", None), d.get("description", ""))
                    for d in items]
        finally:
            if self._client is None:
                await client.aclose()

    async def pause_dag(self, dag_id) -> None:
        await self._set_paused(dag_id, True)

    async def unpause_dag(self, dag_id) -> None:
        await self._set_paused(dag_id, False)

    async def _set_paused(self, dag_id, paused) -> None:
        client = self._client or httpx.AsyncClient()
        try:
            r = await client.patch(
                f"{self.base_url}/dags/{dag_id}",
                json={"is_paused": paused}, headers=self.headers)
            r.raise_for_status()
        finally:
            if self._client is None:
                await client.aclose()

    def detect_schedule_conflict(self, cron, task_resource) -> list:
        from app.infra.llm_router import get_llm
        conflicts = []
        known_heavy = {"dag_etl_daily", "dag_etl_hourly"}
        if task_resource in known_heavy and cron.startswith("0 0"):
            conflicts.append({"resource": task_resource,
                              "reason": "heavy job at midnight may contend with daily batch"})
        return conflicts


def get_scheduler() -> AirflowScheduler:
    return AirflowScheduler()
