"""P5-03: ETL schedule & dependency configuration."""
from dataclasses import dataclass, field


@dataclass
class ScheduleConfig:
    cron: str = "0 30 2 * * *"
    upstream_dependencies: list = field(default_factory=list)
    retries: int = 3
    retry_delay_minutes: int = 5
    timeout_minutes: int = 60
    resource_pool: str = "default"
    alert_on_failure: bool = True
    alert_recipients: list = field(default_factory=list)


@dataclass
class ConflictWarning:
    severity: str = "info"
    message: str = ""
    suggested_fix: str = ""

    def to_dict(self):
        return {"severity": self.severity, "message": self.message,
                "suggested_fix": self.suggested_fix}


_UPSTREAM_PROD_TIME = {
    "ods_order": "01:30", "ods_payment": "01:45", "ods_user": "02:00",
}


class ETLScheduler:
    def __init__(self, scheduler=None):
        self.scheduler = scheduler

    async def generate_schedule(self, etl_target, source_tables) -> ScheduleConfig:
        upstream = self._find_upstream_dags(source_tables)
        cron = self._recommend_cron(upstream)
        owners = self._infer_owners(etl_target)
        return ScheduleConfig(cron=cron, upstream_dependencies=upstream,
                              alert_recipients=owners)

    def _find_upstream_dags(self, source_tables):
        return [f"dag_{s}" for s in source_tables if s in _UPSTREAM_PROD_TIME]

    @staticmethod
    def _recommend_cron(upstream):
        latest = 0
        for dag in upstream:
            src = dag.replace("dag_", "")
            t = _UPSTREAM_PROD_TIME.get(src, "02:00")
            hh, mm = int(t.split(":")[0]), int(t.split(":")[1])
            latest = max(latest, hh * 60 + mm)
        start = latest + 30  # +30min buffer
        hh, mm = start // 60, start % 60
        return f"{mm} {hh} * * *"

    @staticmethod
    def _infer_owners(etl_target):
        return ["etl-owner@corp.com"]

    async def detect_conflicts(self, schedule) -> list:
        warnings = []
        if self.scheduler is not None:
            try:
                conflicts = self.scheduler.detect_schedule_conflict(
                    schedule.cron, schedule.resource_pool)
                for c in conflicts:
                    warnings.append(ConflictWarning(
                        severity="warning",
                        message="resource contended at same time slot",
                        suggested_fix="move to a different hour"))
            except Exception:
                pass
        if schedule.resource_pool == "heavy" and schedule.cron.startswith("0 0"):
            warnings.append(ConflictWarning(
                severity="critical", message="midnight heavy load",
                suggested_fix="schedule after 02:00"))
        return warnings


_etl_scheduler = None


def get_etl_scheduler() -> ETLScheduler:
    global _etl_scheduler
    if _etl_scheduler is None:
        _etl_scheduler = ETLScheduler()
    return _etl_scheduler
