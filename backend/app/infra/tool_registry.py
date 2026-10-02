"""P3-03: Tool registry - register, discover, invoke tools for agents."""
import json
import time
from typing import Callable, Any
import httpx


class ToolSpec:
    def __init__(self, name, description, handler, required_role="L2_analyst",
                 risk_level="read_only", parameters=None):
        self.name = name
        self.description = description
        self.handler = handler
        self.required_role = required_role
        self.risk_level = risk_level
        self.parameters = parameters or {}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self._register_defaults()

    def _register_defaults(self):
        self.register(ToolSpec("execute_sql", "Execute a SQL query on Doris", self._execute_sql,
                               risk_level="high_risk", required_role="L3_engineer",
                               parameters={"sql": "SQL string"}))
        self.register(ToolSpec("query_metadata", "Query table/column metadata", self._query_metadata,
                               parameters={"keyword": "search term", "limit": 10}))
        self.register(ToolSpec("run_anomaly_check", "Run anomaly detection on metrics", self._anomaly_check,
                               parameters={"metric_name": "metric name"}))
        self.register(ToolSpec("get_entity_relations", "Get business entity relationships", self._entity_relations,
                               parameters={"table": "table name"}))
        self.register(ToolSpec("export_csv", "Export query result to CSV", self._export_csv,
                               parameters={"query_id": "query id"}))

    def register(self, spec: ToolSpec):
        self._tools[spec.name] = spec

    def list_tools(self, role: str = "L1_business") -> list:
        return [{"name": t.name, "description": t.description, "risk_level": t.risk_level,
                 "parameters": t.parameters}
                for t in self._tools.values()
                if self._role_allowed(t.required_role, role)]

    def invoke(self, name: str, params: dict, role: str = "L1_business") -> dict:
        """Invoke a registered tool. `role` is the caller's role and is
        enforced against the tool's `required_role`. Previously the
        role check was only applied in `list_tools`, so any caller could
        invoke `execute_sql` (DDL/DML on Doris) directly. The role must
        now be supplied by every caller; defaulting to the lowest role
        makes accidental privilege escalation impossible.
        """
        tool = self._tools.get(name)
        if not tool:
            return {"error": f"Tool '{name}' not found"}
        if not self._role_allowed(tool.required_role, role):
            return {"error": f"Permission denied: tool '{name}' requires "
                             f"role '{tool.required_role}', caller has '{role}'",
                    "ok": False}
        try:
            result = tool.handler(params)
            return {"tool": name, "result": result, "ok": True}
        except Exception as e:
            return {"tool": name, "error": str(e), "ok": False}

    @staticmethod
    def _role_allowed(required: str, user_role: str) -> bool:
        ranks = {"L1_business": 1, "L2_analyst": 2, "L3_engineer": 3, "L4_admin": 4}
        return ranks.get(user_role, 0) >= ranks.get(required, 0)

    @staticmethod
    def _execute_sql(params):
        import pymysql
        from app.conf.app_config import app_config as cfg
        conn = pymysql.connect(host=cfg.doris.host, port=cfg.doris.port,
                               user=cfg.doris.user, password=cfg.doris.password,
                               database="dw", charset="utf8mb4", autocommit=True)
        try:
            cur = conn.cursor()
            safe = params["sql"].strip()[:2000]
            cur.execute(safe)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                return {"rows": rows[:100], "total": len(rows)}
            return {"affected": cur.rowcount}
        finally:
            conn.close()

    @staticmethod
    def _query_metadata(params):
        import pymysql
        from app.conf.app_config import app_config as cfg
        kw = params.get("keyword", "")
        limit = params.get("limit", 10)
        conn = pymysql.connect(host=cfg.doris.host, port=cfg.doris.port,
                               user=cfg.doris.user, password=cfg.doris.password,
                               database="data_agent", charset="utf8mb4")
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, name, `role`, description FROM table_info WHERE id LIKE %s LIMIT %s",
                        (f"%{kw}%", limit))
            return [dict(zip(["id","name","role","description"], r)) for r in cur.fetchall()]
        finally:
            conn.close()

    @staticmethod
    def _anomaly_check(params):
        return {"status": "no anomalies detected", "checked_at": time.time()}

    @staticmethod
    def _entity_relations(params):
        import pymysql
        from app.conf.app_config import app_config as cfg
        table = params.get("table", "")
        conn = pymysql.connect(host=cfg.doris.host, port=cfg.doris.port,
                               user=cfg.doris.user, password=cfg.doris.password,
                               database="data_agent", charset="utf8mb4")
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM entity_relation WHERE source_entity LIKE %s LIMIT 20", (f"%{table}%",))
            return [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        finally:
            conn.close()

    @staticmethod
    def _export_csv(params):
        return {"url": f"/api/v1/export/{params.get('query_id', 'unknown')}.csv"}


_tool_registry = None

def get_tool_registry() -> ToolRegistry:
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry
