from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_query_service
from app.api.schemas.query_schema import QuerySchema, MAX_QUERY_LENGTH
import logging
_query_logger = logging.getLogger(__name__)
from app.core.auth import verify_token
from app.services.query_service import QueryService

query_router = APIRouter()


@query_router.post("/api/query")
async def query(
    query: QuerySchema,
    request: Request,
    user: dict = Depends(verify_token),
    service: QueryService = Depends(get_query_service),
):
    return StreamingResponse(
        q = query.query
if len(q) > MAX_QUERY_LENGTH:
    _query_logger.warning(f"Query truncated from {len(q)} to {MAX_QUERY_LENGTH}")
    q = q[:MAX_QUERY_LENGTH]
service.query(q, query.history, username=user.get("sub")),
        media_type="text/event-stream",
    )
