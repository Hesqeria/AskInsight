"""P1-08: Multi-agent orchestration API - dispatch, execute, health."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import verify_token
from app.orchestrator.factory import get_wired_registry
from app.orchestrator.multi_agent import get_orchestrator

orchestrator_router = APIRouter()


class DispatchRequest(BaseModel):
    query: str
    intent: str = "data_query"
    metric: Optional[str] = None
    table: Optional[str] = None
    text: Optional[str] = None


@orchestrator_router.post("/orchestrator/dispatch")
async def dispatch(
    req: DispatchRequest,
    user: dict = Depends(verify_token),
):
    orch = get_orchestrator()
    get_wired_registry()
    request = {"query_id": None, "user_id": user.get("sub", ""), "role": user.get("role", "L2_analyst")}
    intent = {"intent": req.intent, "query": req.query, "metric": req.metric,
              "table": req.table, "text": req.text}
    d = orch.dispatch(request, intent)
    return d.to_dict()


@orchestrator_router.post("/orchestrator/execute")
async def execute(
    req: DispatchRequest,
    user: dict = Depends(verify_token),
):
    orch = get_orchestrator()
    reg = get_wired_registry()
    orch.registry = reg
    request = {"query_id": None, "user_id": user.get("sub", ""), "role": user.get("role", "L2_analyst")}
    intent = {"intent": req.intent, "query": req.query, "metric": req.metric,
              "table": req.table, "text": req.text}
    d = orch.dispatch(request, intent)
    result = await orch.execute_dispatch(d, request, intent)
    return {"dispatch": d.to_dict(), "result": result.to_dict()}


@orchestrator_router.get("/orchestrator/agents")
async def list_agents(
    user: dict = Depends(verify_token),
):
    reg = get_wired_registry()
    return {"agents": reg.list()}


@orchestrator_router.get("/orchestrator/health")
async def agent_health(
    user: dict = Depends(verify_token),
):
    orch = get_orchestrator()
    reg = get_wired_registry()
    orch.registry = reg
    health = await orch.get_agent_health()
    return {"health": health}
