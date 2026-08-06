"""Admin API: chart recommendation + LLM switching + Dashboard management."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from app.core.auth import verify_token
from app.core.log import logger

admin_router = APIRouter(prefix="/api/admin")


# === LLM management ===

class LLMConfigSchema(BaseModel):
    provider: str = "deepseek"
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@admin_router.get("/llm/providers")
async def list_llm_providers(user: dict = Depends(verify_token)):
    """List supported LLM providers."""
    from app.agent.llm_provider import SUPPORTED_PROVIDERS
    return {"providers": list(SUPPORTED_PROVIDERS.keys())}


@admin_router.post("/llm/switch")
async def switch_llm(config: LLMConfigSchema, user: dict = Depends(verify_token)):
    """Switch LLM provider (runtime)."""
    from app.agent.llm_provider import build_llm
    import app.agent.llm as llm_module
    try:
        new_llm = build_llm(
            provider=config.provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
        )
        llm_module.llm = new_llm
        logger.info(f"LLM switched: provider={config.provider}, model={config.model}")
        return {"status": "ok", "provider": config.provider, "model": config.model}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# === Dashboard management ===

@admin_router.get("/dashboards")
async def list_dashboards(user: dict = Depends(verify_token)):
    """Get the list of Superset dashboards."""
    from app.services.superset_service import get_dashboards
    return {"dashboards": get_dashboards()}


class DashboardCreateSchema(BaseModel):
    title: str
    queries: list[dict]  # [{"sql": "...", "name": "...", "chart_type": "bar"}]


@admin_router.post("/dashboard/create")
async def create_dashboard(body: DashboardCreateSchema, user: dict = Depends(verify_token)):
    """Create a Superset dashboard from query results."""
    from app.services.superset_service import create_dashboard_from_queries
    result = create_dashboard_from_queries(body.title, body.queries)
    return result


# === Chart recommendation ===

class ChartRecSchema(BaseModel):
    query: str
    data: list[dict]


@admin_router.post("/chart/recommend")
async def recommend_chart(body: ChartRecSchema, user: dict = Depends(verify_token)):
    """Recommend chart type based on data (rule-based, no LLM call)."""
    from app.agent.nodes.chart_recommender import _detect_chart_heuristic
    chart_type = _detect_chart_heuristic(body.data)
    return {"chart_type": chart_type, "data_rows": len(body.data)}


class IncrementalUpdateSchema(BaseModel):
    table_name: Optional[str] = None


@admin_router.post("/knowledge/incremental")
async def incremental_update_knowledge(
    body: IncrementalUpdateSchema,
    user: dict = Depends(verify_token),
):
    """Incrementally update the knowledge base (no full rebuild).

    - Without table_name: update all tables
    - With table_name: only update the specified table
    """
    import asyncio
    from pathlib import Path
    from app.scripts.incremental_update import incremental_update

    config_path = Path(__file__).parents[3] / "conf" / "meta_config.yaml"

    try:
        # Run in background (does not block the response)
        asyncio.create_task(incremental_update(config_path, body.table_name))
        return {
            "status": "started",
            "message": f"Incremental update started {'(table: ' + body.table_name + ')' if body.table_name else '(all tables)'}",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


class StatsSchema(BaseModel):
    data: list[dict]
    operation: str
    params: dict = {}


@admin_router.post("/api/admin/stats")
async def run_stats(body: StatsSchema, user: dict = Depends(verify_token)):
    """Statistical analysis (built-in functions, no LLM call).

    operation: describe/growth/breakdown/rank/correlation/cross
    """
    from app.services.stats_service import (
        describe, growth_rate, percentage_breakdown,
        rank_with_gap, correlation, cross_analysis
    )

    ops = {
        "describe": lambda: describe([r.get(body.params.get("col", ""), 0) for r in body.data]),
        "growth": lambda: growth_rate(body.params.get("current", 0), body.params.get("previous", 0)),
        "breakdown": lambda: percentage_breakdown(body.data, body.params.get("category", ""), body.params.get("value", "")),
        "rank": lambda: rank_with_gap(body.data, body.params.get("value", "")),
        "correlation": lambda: correlation(body.data, body.params.get("x", ""), body.params.get("y", "")),
        "cross": lambda: cross_analysis(body.data, body.params.get("row", ""), body.params.get("col", ""), body.params.get("value", "")),
    }

    func = ops.get(body.operation)
    if not func:
        return {"error": f"Unsupported operation: {body.operation}, available: {list(ops.keys())}"}

    try:
        result = func()
        return {"operation": body.operation, "result": result}
    except Exception as e:
        return {"error": str(e)}
