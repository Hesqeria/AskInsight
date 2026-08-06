"""ChartOp: renders chart config from query results."""

from typing import Any
from app.workflow.operators.base import MapOp


class ChartOp(MapOp):
    """Generate ECharts configuration from SQL results."""

    def __init__(self, name: str = "chart", app=None):
        super().__init__(name)
        self._app = app

    async def map(self, data: Any) -> dict:
        return {"type": "table", "data": data, "chartConfig": {}}
