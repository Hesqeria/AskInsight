"""Modeling advisor API."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.auth import verify_token
from app.services.model_advisor import advise_modeling

model_router = APIRouter()


class ModelRequest(BaseModel):
    description: str
    tables: dict = {}


@model_router.post("/api/model/advise")
async def advise(req: ModelRequest, user=Depends(verify_token)):
    try:
        result = advise_modeling(req.description, req.tables)
        return result
    except Exception as e:
        return {"error": str(e)[:200]}
