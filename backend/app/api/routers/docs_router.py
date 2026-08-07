"""ETL documentation generation API."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.auth import verify_token
from app.services.doc_service import generate_etl_doc

docs_router = APIRouter()


class ETLRequest(BaseModel):
    sql: str
    layer: str = "DWD"


@docs_router.post("/api/docs/etl")
async def gen_etl_doc(req: ETLRequest, user=Depends(verify_token)):
    try:
        doc = generate_etl_doc(req.sql, req.layer)
        return {"doc": doc}
    except Exception as e:
        return {"error": str(e)[:200]}
