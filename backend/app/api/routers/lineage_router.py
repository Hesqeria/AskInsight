"""Lineage analysis API."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.auth import verify_token
from app.services.lineage_service import extract_table_lineage, extract_column_lineage

lineage_router = APIRouter()


class LineageRequest(BaseModel):
    sql: str


@lineage_router.post("/api/lineage")
async def analyze_lineage(req: LineageRequest, user=Depends(verify_token)):
    try:
        tables = extract_table_lineage(req.sql)
        columns = extract_column_lineage(req.sql)
        return {"tables": tables, "columns": columns}
    except Exception as e:
        return {"error": str(e)[:200]}
