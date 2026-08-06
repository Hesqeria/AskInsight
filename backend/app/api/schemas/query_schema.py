from typing import Optional
from pydantic import BaseModel, Field


class QuerySchema(BaseModel):
    query: str = Field(..., description="User natural-language query")
    history: Optional[list[str]] = Field(default=[], description="Historical query list (for multi-turn conversation)")
