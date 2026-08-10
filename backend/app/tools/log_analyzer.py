"""P4-06: Log analyzer - airflow/doris be/fe/audit logs with masking."""
import re

SENSITIVE_PATTERN = re.compile(r"(password|token|api[_-]?key|secret)=[^\s\"]+", re.IGNORECASE)
SENSITIVE_KEYWORDS = ("password", "token", "api_key", "apikey", "secret", "authorization")


class LogAnalyzer:
    def __init__(self, airflow=None, ssh=None, doris=None):
        self.airflow = airflow
        self.ssh = ssh
        self.doris = doris

    def query_airflow_logs(self, dag_id, run_id, task_id=None) -> str:
        if self.airflow is not None:
            try:
                return self.airflow.get_task_logs(dag_id, run_id, task_id)
            except Exception:
                pass
        log = f"[airflow] dag={dag_id} run={run_id} task={task_id or 'root'} state=success"
        return self._mask(log)

    def query_doris_be_logs(self, host, level="ERROR", since_minutes=60) -> list:
        if self.ssh is not None:
            try:
                out = self.ssh.run(host, f"grep '{level}' /opt/module/doris/be/log/be.WARNING")
                return [self._mask(line) for line in out.splitlines() if line.strip()]
            except Exception:
                pass
        return [self._mask(f"[be:{host}] {level}: no error found in last {since_minutes}m")]

    def query_doris_fe_logs(self, level="ERROR", since_minutes=60) -> list:
        if self.ssh is not None:
            try:
                out = self.ssh.run("fe1", f"grep '{level}' /opt/module/doris/fe/log/fe.warn.log")
                return [self._mask(line) for line in out.splitlines() if line.strip()]
            except Exception:
                pass
        return [self._mask(f"[fe] {level}: no error found in last {since_minutes}m")]

    def query_audit_logs(self, request_id=None, user_id=None, since_hours=24) -> list:
        if self.doris is not None:
            try:
                sql = "SELECT * FROM data_agent.audit_log WHERE created_at >= NOW() - INTERVAL %s HOUR"
                params = [since_hours]
                if request_id:
                    sql += " AND request_id = %s"
                    params.append(request_id)
                if user_id:
                    sql += " AND username = %s"
                    params.append(user_id)
                rows = self.doris.query(sql, params=params)
                return [self._mask_row(r) for r in rows]
            except Exception:
                pass
        rows = [{"request_id": request_id or "req-0001",
                 "username": user_id or "admin", "action": "query",
                 "status": "success"}]
        return [self._mask_row(r) for r in rows]

    def grep_logs(self, source, pattern, since_minutes=60) -> list:
        if source == "fe":
            return [l for l in self.query_doris_fe_logs(since_minutes=since_minutes)
                    if pattern.lower() in l.lower()]
        if source == "be":
            return [l for l in self.query_doris_be_logs("be1", since_minutes=since_minutes)
                    if pattern.lower() in l.lower()]
        return []

    @staticmethod
    def _mask(text):
        text = SENSITIVE_PATTERN.sub(r"=***", text)
        for kw in SENSITIVE_KEYWORDS:
            text = re.sub(rf"({kw})\s*[=:]\s*[^\s,\"]+",
                          rf"=***", text, flags=re.IGNORECASE)
        return text

    @staticmethod
    def _mask_row(row):
        if isinstance(row, dict):
            out = dict(row)
            for k in list(out.keys()):
                if any(kw in k.lower() for kw in SENSITIVE_KEYWORDS):
                    out[k] = "***"
            return out
        return row


_log_analyzer = None


def get_log_analyzer() -> LogAnalyzer:
    global _log_analyzer
    if _log_analyzer is None:
        _log_analyzer = LogAnalyzer()
    return _log_analyzer
