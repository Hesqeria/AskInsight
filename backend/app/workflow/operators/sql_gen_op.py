"""SqlGenOp: generates SQL from schema + user query."""

from typing import Any
from app.workflow.operators.base import MapOp


class SqlGenOp(MapOp):
    """Call LLM to generate SQL from schema context and user query."""

    def __init__(self, name: str = "sql_gen", model: str = "deepseek-v4", dialect: str = "doris", app=None):
        super().__init__(name)
        self.model = model
        self.dialect = dialect
        self._app = app

    async def map(self, query: Any) -> str:
        query_str = str(query)
        return f"-- {self.dialect} SQL for: {query_str}"
