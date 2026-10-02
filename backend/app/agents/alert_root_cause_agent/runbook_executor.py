"""P6-05: Runbook execution engine - risk-gated auto repair."""
from dataclasses import dataclass, field


@dataclass
class RunbookStep:
    step_id: int
    action: str
    parameters: dict = field(default_factory=dict)
    verify_condition: str = None
    rollback_action: dict = None


@dataclass
class Runbook:
    runbook_id: str
    name: str
    description: str = ""
    trigger_conditions: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)
    risk_level: str = "low_risk"
    required_role: str = "L3_engineer"


class ExecutionResult:
    def __init__(self, status, failed_step=None, error=None, ticket_id=None, message=""):
        self.status = status
        self.failed_step = failed_step
        self.error = error
        self.ticket_id = ticket_id
        self.message = message

    def to_dict(self):
        return {"status": self.status, "failed_step": self.failed_step,
                "error": self.error, "ticket_id": self.ticket_id,
                "message": self.message}


class RunbookExecutor:
    def __init__(self, scheduler=None, notifier=None, doris=None, ssh=None,
                 approvals=None, incident_verifier=None):
        """`incident_verifier` is an optional async callable that takes an
        Incident and returns True if the incident is genuinely resolved
        (e.g. by re-querying the live alerting system). When None, the
        executor falls back to checking `incident.status` directly.
        """
        self.scheduler = scheduler
        self.notifier = notifier
        self.doris = doris
        self.ssh = ssh
        self.approvals = approvals
        self.incident_verifier = incident_verifier
        self._steps_run = []

    async def execute(self, runbook, incident, context=None) -> ExecutionResult:
        if runbook.risk_level == "high_risk":
            if self.approvals is None:
                return ExecutionResult("pending_approval", ticket_id="ticket_manual")
            ticket = self.approvals.submit(runbook.runbook_id, incident.incident_id)
            return ExecutionResult("pending_approval", ticket_id=ticket)

        for step in runbook.steps:
            try:
                ok = await self._run_step(step, incident)
                if step.verify_condition and not ok:
                    await self._rollback(runbook, step.step_id)
                    return ExecutionResult("failed", failed_step=step.step_id,
                                           error="verify_condition not met")
            except Exception as e:
                await self._rollback(runbook, step.step_id)
                return ExecutionResult("failed", failed_step=step.step_id, error=str(e))

        resolved = await self._verify_incident_resolved(incident)
        if resolved:
            incident.status = "resolved"
            return ExecutionResult("success")
        return ExecutionResult("partial", message="runbook done but incident not resolved")

    async def _run_step(self, step, incident):
        self._steps_run.append(step.step_id)
        action = step.action
        params = step.parameters
        if action == "airflow_trigger":
            if self.scheduler is not None:
                await self.scheduler.trigger_dag(params.get("dag_id"),
                                                 params.get("conf"))
                return True
            return True
        if action == "sql_exec":
            if self.doris is not None:
                self.doris.execute(params.get("sql", ""))
            return True
        if action == "notify":
            if self.notifier is not None:
                await self.notifier.notify(params.get("channel", "dingtalk"),
                                           params.get("recipients", []),
                                           params.get("title", ""),
                                           params.get("content", ""),
                                           params.get("severity", "info"))
            return True
        if action == "ssh_cmd":
            if self.ssh is not None:
                self.ssh.run(params.get("host"), params.get("cmd", ""))
            return True
        if action == "wait":
            return True
        return False

    async def _rollback(self, runbook, failed_step_id):
        for step in runbook.steps:
            if step.step_id > failed_step_id and step.rollback_action:
                await self._run_rollback(step.rollback_action)
        return True

    async def _run_rollback(self, action):
        rb_step = RunbookStep(step_id=-1, action=action.get("action", "notify"),
                              parameters=action.get("parameters", {}))
        await self._run_step(rb_step, None)

    async def _verify_incident_resolved(self, incident):
        # Prefer an external verifier that re-queries the live alerting
        # system; otherwise fall back to the incident's current status.
        # An incident is considered resolved only when its status is no
        # longer "active" (e.g. "resolved", "closed"). Previously this
        # check was inverted (`== "active"`), which caused every
        # successful runbook to mark the incident resolved regardless of
        # the actual outcome.
        if self.incident_verifier is not None:
            return bool(await self.incident_verifier(incident))
        return incident.status != "active"


_executor = None


def get_runbook_executor() -> RunbookExecutor:
    global _executor
    if _executor is None:
        _executor = RunbookExecutor()
    return _executor
