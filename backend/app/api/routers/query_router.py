from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_query_service
from app.api.schemas.query_schema import QuerySchema
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
        service.query(query.query, query.history, username=user.get("sub")),
        media_type="text/event-stream",
    )
