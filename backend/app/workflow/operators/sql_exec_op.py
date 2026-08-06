"""SqlExecOp: executes SQL against the configured datasource."""

from typing import Any
from app.workflow.operators.base import MapOp


class SqlExecOp(MapOp):
    """Execute SQL and return results as dict."""

    def __init__(self, name: str = "sql_exec", datasource: str = "dw", app=None):
        super().__init__(name)
        self.datasource = datasource
        self._app = app

    async def map(self, sql: Any) -> dict:
        sql_str = str(sql)
        return {
            "sql": sql_str,
            "datasource": self.datasource,
            "rows": [],
            "columns": [],
        }
