"""SchemaRecallOp: retrieves relevant tables for a query."""

from typing import Any
from app.workflow.operators.base import MapOp


class SchemaRecallOp(MapOp):
    """Recall relevant DB table schema for the user query."""

    def __init__(self, name: str = "schema", limit: int = 10, app=None):
        super().__init__(name)
        self.limit = limit
        self._app = app

    async def map(self, query: Any) -> list:
        if isinstance(query, dict):
            query = query.get("content", str(query))
        query = str(query)

        if self._app and hasattr(self._app, "state"):
            tables = getattr(self._app.state, "table_meta", [])
            return [t.get("name", t) for t in tables[:self.limit]]

        return ["default_table"]
