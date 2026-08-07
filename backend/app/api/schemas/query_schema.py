from typing import Optional
from pydantic import BaseModel, Field, field_validator


MAX_QUERY_LENGTH = 4000


class QuerySchema(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="User natural-language query")
    history: Optional[list[str]] = Field(default=[], description="Historical query list")

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Query cannot be empty or whitespace-only")
        return stripped
