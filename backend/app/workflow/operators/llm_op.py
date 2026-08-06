"""LLMCallOp: raw LLM call for analysis/alert generation."""

from typing import Any
from app.workflow.operators.base import MapOp


class LLMCallOp(MapOp):
    """Call LLM for analysis tasks like cohort analysis or anomaly detection."""

    def __init__(self, name: str = "llm_call", prompt_type: str = "default", model: str = "deepseek-v4", app=None):
        super().__init__(name)
        self.prompt_type = prompt_type
        self.model = model
        self._app = app

    async def map(self, data: Any) -> dict:
        return {"type": self.prompt_type, "result": str(data)[:200], "model": self.model}
