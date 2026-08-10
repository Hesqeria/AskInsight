import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Request, Query as QueryParam
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.api.dependencies import get_query_service
from app.api.schemas.query_schema import QuerySchema, MAX_QUERY_LENGTH
from app.core.auth import verify_token
from app.services.query_service import QueryService

_query_logger = logging.getLogger(__name__)
query_router = APIRouter()


class FilterItem(BaseModel):
    field: str
    operator: str = "eq"
    value: str


class ExecuteRequest(BaseModel):
    sql: str
    filters: List[FilterItem] = []


@query_router.post("/api/query")
async def query(
    query: QuerySchema,
    request: Request,
    user: dict = Depends(verify_token),
    service: QueryService = Depends(get_query_service),
):
    q = query.query
    if len(q) > MAX_QUERY_LENGTH:
        _query_logger.warning(f"Query truncated from {len(q)} to {MAX_QUERY_LENGTH}")
        q = q[:MAX_QUERY_LENGTH]
    return StreamingResponse(
        service.query(q, query.history, username=user.get("sub")),
        media_type="text/event-stream",
    )


@query_router.get("/api/query/filters/{table}/{column}")
async def get_filters(
    table: str,
    column: str,
    limit: int = QueryParam(100, ge=1, le=1000),
    offset: int = QueryParam(0, ge=0),
    search: Optional[str] = QueryParam(None),
    user: dict = Depends(verify_token),
    service: QueryService = Depends(get_query_service),
):
    values = await service.get_distinct_values(table, column, limit=limit, offset=offset, search=search or "")
    return JSONResponse({"values": values, "limit": limit, "offset": offset})


@query_router.post("/api/query/execute")
async def execute_sql(
    body: ExecuteRequest,
    user: dict = Depends(verify_token),
    service: QueryService = Depends(get_query_service),
):
    filters = [f.model_dump() for f in body.filters]
    rows = await service.execute_filtered_sql(body.sql, filters)
    return JSONResponse({"rows": rows})
