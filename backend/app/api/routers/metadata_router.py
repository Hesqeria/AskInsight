"""P4-02: Metadata query API - tables/columns/metrics/glossary/lineage/relations."""
from typing import Optional

from fastapi import APIRouter, Depends, Query as QueryParam
from pydantic import BaseModel

from app.core.auth import verify_token
from app.tools.metadata_query import get_metadata_api

metadata_router = APIRouter()


class SemanticSearchRequest(BaseModel):
    query: str
    top_k: int = 10


@metadata_router.get("/metadata/tables")
async def list_tables(
    role: Optional[str] = QueryParam(None),
    layer: Optional[str] = QueryParam(None),
    user: dict = Depends(verify_token),
):
    api = get_metadata_api()
    return {"tables": api.list_tables(role=role, layer=layer)}


@metadata_router.get("/metadata/tables/{table_id}")
async def get_table(
    table_id: str,
    user: dict = Depends(verify_token),
):
    api = get_metadata_api()
    t = api.get_table(table_id)
    if t is None:
        return {"error": f"table not found: {table_id}"}
    return t.to_dict()


@metadata_router.post("/metadata/columns/search")
async def search_columns(
    req: SemanticSearchRequest,
    user: dict = Depends(verify_token),
):
    api = get_metadata_api()
    results = api.search_columns_by_semantic(req.query, top_k=req.top_k)
    return {"query": req.query, "results": results}


@metadata_router.get("/metrics")
async def list_metrics(
    alias: Optional[str] = QueryParam(None),
    user: dict = Depends(verify_token),
):
    api = get_metadata_api()
    if alias:
        return {"metrics": api.search_metrics_by_alias(alias)}
    metrics = [api.get_metric(m.id) for m in api._BUILTIN_METRICS]
    return {"metrics": [m.to_dict() for m in metrics if m]}


@metadata_router.get("/glossary")
async def get_glossary(
    term: str = QueryParam(..., min_length=1),
    user: dict = Depends(verify_token),
):
    api = get_metadata_api()
    g = api.get_glossary(term)
    if g is None:
        return {"error": f"term not found: {term}"}
    return g.to_dict()


@metadata_router.get("/metadata/lineage")
async def get_lineage(
    table: str = QueryParam(...),
    direction: str = QueryParam("upstream", pattern="^(upstream|downstream)$"),
    depth: int = QueryParam(2, ge=1, le=5),
    user: dict = Depends(verify_token),
):
    api = get_metadata_api()
    return api.get_lineage(table, direction=direction, depth=depth)


@metadata_router.get("/metadata/relations")
async def get_relations(
    entity: str = QueryParam(...),
    depth: int = QueryParam(1, ge=1, le=3),
    user: dict = Depends(verify_token),
):
    api = get_metadata_api()
    return api.get_relations(entity, depth=depth)
