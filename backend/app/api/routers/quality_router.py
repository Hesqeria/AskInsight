"""Quality rule generation API."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.auth import verify_token
from app.services.quality_service import generate_quality_rules, generate_batch

quality_router = APIRouter()


class DDLRequest(BaseModel):
    ddl: str


@quality_router.post("/api/quality/generate")
async def gen_quality(req: DDLRequest, user=Depends(verify_token)):
    try:
        result = generate_quality_rules(req.ddl)
        batch_sql = generate_batch(result)
        return {**result, "batch_sql": batch_sql}
    except Exception as e:
        return {"error": str(e)[:200]}
