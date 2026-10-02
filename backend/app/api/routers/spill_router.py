"""Large-result spill API (PRD M10 FR2).

  GET /api/v1/spills/{spill_id}?offset=&limit=   paginated full result

The "查看全量" frontend action hits this after receiving a spill_id in
the execute_sql SSE frame.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.core.auth import verify_token
from app.services.spill_store import get_spill

spill_router = APIRouter()


@spill_router.get("/api/v1/spills/{spill_id}")
async def get_spilled_result(
    spill_id: str,
    offset: int = 0,
    limit: int = 100,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    if offset < 0 or limit < 1 or limit > 1000:
        return JSONResponse(status_code=422, content={
            "error": "offset >= 0 and 1 <= limit <= 1000 required"})
    result = await get_spill(session, spill_id, offset=offset, limit=limit)
    if not result.get("found"):
        return JSONResponse(status_code=404, content={
            "error": f"spill {spill_id!r} not found"})
    return result
