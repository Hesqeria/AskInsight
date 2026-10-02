"""管理 API：图表推荐 + LLM 切换 + Dashboard 管理"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from app.core.auth import require_admin
from app.core.log import logger

admin_router = APIRouter(prefix="/api/admin")


# === LLM 管理 ===

class LLMConfigSchema(BaseModel):
    provider: str = "deepseek"
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@admin_router.get("/llm/providers")
async def list_llm_providers(user: dict = Depends(require_admin)):
    """列出支持的 LLM 服务商"""
    from app.agent.llm_provider import SUPPORTED_PROVIDERS
    return {"providers": list(SUPPORTED_PROVIDERS.keys())}


@admin_router.post("/llm/switch")
async def switch_llm(config: LLMConfigSchema, user: dict = Depends(require_admin)):
    """切换 LLM 服务商（运行时）"""
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
        logger.info(f"LLM 切换: provider={config.provider}, model={config.model}")
        return {"status": "ok", "provider": config.provider, "model": config.model}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# === Dashboard 管理 ===

@admin_router.get("/dashboards")
async def list_dashboards(user: dict = Depends(require_admin)):
    """获取 Superset Dashboard 列表"""
    from app.services.superset_service import get_dashboards
    return {"dashboards": await get_dashboards()}


class DashboardCreateSchema(BaseModel):
    title: str
    queries: list[dict]  # [{"sql": "...", "name": "...", "chart_type": "bar"}]


@admin_router.post("/dashboard/create")
async def create_dashboard(body: DashboardCreateSchema, user: dict = Depends(require_admin)):
    """从查询结果创建 Superset Dashboard"""
    from app.services.superset_service import create_dashboard_from_queries
    result = await create_dashboard_from_queries(body.title, body.queries)
    return result


# === 图表推荐 ===

class ChartRecSchema(BaseModel):
    query: str
    data: list[dict]


@admin_router.post("/chart/recommend")
async def recommend_chart(body: ChartRecSchema, user: dict = Depends(require_admin)):
    """根据数据推荐图表类型（不调用 LLM，纯规则）"""
    from app.agent.nodes.chart_recommender import _detect_chart_heuristic
    chart_type = _detect_chart_heuristic(body.data)
    return {"chart_type": chart_type, "data_rows": len(body.data)}


class IncrementalUpdateSchema(BaseModel):
    table_name: Optional[str] = None


@admin_router.post("/knowledge/incremental")
async def incremental_update_knowledge(
    body: IncrementalUpdateSchema,
    user: dict = Depends(require_admin),
):
    """增量更新知识库（不全量重建）

    - 无 table_name: 更新全部表
    - 有 table_name: 仅更新指定表
    """
    import asyncio
    from pathlib import Path
    from app.scripts.incremental_update import incremental_update

    config_path = Path(__file__).parents[3] / "conf" / "meta_config.yaml"

    try:
        async def _safe_incremental_update(config_path, table_name):
            try:
                await incremental_update(config_path, table_name)
                logger.info('Incremental update completed')
            except Exception as e:
                logger.error(f'Incremental update failed: {e}')

        # 后台执行（不阻塞响应）
        asyncio.create_task(_safe_incremental_update(config_path, body.table_name))
        return {
            "status": "started",
            "message": f"增量更新已启动 {'(表: ' + body.table_name + ')' if body.table_name else '(全部表)'}",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


class StatsSchema(BaseModel):
    data: list[dict]
    operation: str
    params: dict = {}


@admin_router.post("/stats")
async def run_stats(body: StatsSchema, user: dict = Depends(require_admin)):
    """统计分析（内置函数，不调用 LLM）

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
        return {"error": f"不支持的操作: {body.operation}, 可选: {list(ops.keys())}"}

    try:
        result = func()
        return {"operation": body.operation, "result": result}
    except Exception as e:
        return {"error": str(e)}

@admin_router.post("/schema/discover")
async def discover_schema(db_name: str = "dw", user: dict = Depends(require_admin)):
    """Auto-discover database schema and generate meta_config.
    
    Scans the specified database, detects table roles, FK columns,
    and generates ready-to-use meta_config.
    """
    from app.scripts.auto_bootstrap import auto_bootstrap
    from app.clients.doris_client_manager import doris_client_manager
    from app.core.log import logger
    
    try:
        config = await auto_bootstrap(doris_client_manager.session_factory, db_name)
        return {
            "status": "ok",
            "database": db_name,
            "tables": len(config.get("tables", [])),
            "tables_list": [t["name"] for t in config.get("tables", [])],
            "config": config,
        }
    except Exception as e:
        logger.error(f"Schema discovery failed: {e}")
        return {"status": "error", "message": str(e)}

@admin_router.post("/fewshot/generate")
async def generate_fewshot(user: dict = Depends(require_admin)):
    """Generate few-shot SQL examples from current meta_config."""
    from app.scripts.fewshot_generator import generate_fewshot_from_config, format_fewshot_prompt
    try:
        examples = generate_fewshot_from_config(
            config_path="conf/meta_config_dw.yaml"
        )
        prompt_text = format_fewshot_prompt(examples)
        return {
            "status": "ok",
            "count": len(examples),
            "examples": examples,
            "prompt_text": prompt_text,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# === OPT-M3/M4: RBAC + PII 管理（admin only） ===

class RbacUpdateSchema(BaseModel):
    role_name: str
    table_name: str
    allowed: int = 1


@admin_router.get("/rbac/permissions")
async def list_rbac(user: dict = Depends(require_admin),
                    session=Depends(lambda: None)):
    """列出所有 RBAC 权限（按 role 分组）。"""
    from app.api.dependencies import get_meta_session
    from sqlalchemy import text as _text
    from fastapi import Depends as _Depends
    return {"_hint": "use the dependency version below"}


@admin_router.post("/rbac/update")
async def update_rbac(body: RbacUpdateSchema, user: dict = Depends(require_admin)):
    """更新某个 role 对某张表的访问权限（同时清缓存）。"""
    from app.clients.doris_client_manager import doris_client_manager
    from sqlalchemy import text as _text
    doris_client_manager.init()
    try:
        async with doris_client_manager.session_factory() as s:
            await s.execute(_text(
                "DELETE FROM data_agent.role_table_perm WHERE role_name=:r AND table_name=:t"
            ), {"r": body.role_name, "t": body.table_name})
            await s.execute(_text(
                "INSERT INTO data_agent.role_table_perm (role_name, table_name, allowed) VALUES (:r,:t,:a)"
            ), {"r": body.role_name, "t": body.table_name, "a": body.allowed})
            await s.commit()
        from app.services.access_control import invalidate_perm_cache
        invalidate_perm_cache()
        return {"status": "ok", "role_name": body.role_name,
                "table_name": body.table_name, "allowed": body.allowed}
    except Exception as e:
        logger.error(f"update_rbac failed: {e}")
        return {"status": "error", "message": str(e)}


@admin_router.get("/pii/fields")
async def list_pii(user: dict = Depends(require_admin)):
    """列出所有 PII 字段配置。"""
    from app.clients.doris_client_manager import doris_client_manager
    from sqlalchemy import text as _text
    doris_client_manager.init()
    try:
        async with doris_client_manager.session_factory() as s:
            r = await s.execute(_text(
                "SELECT field_id, mask_rule, enabled FROM data_agent.pii_field_config ORDER BY field_id"
            ))
            return {"fields": [{"field_id": row[0], "mask_rule": row[1],
                                "enabled": row[2]} for row in r.fetchall()]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


class PiiUpdateSchema(BaseModel):
    field_id: str
    mask_rule: str = "mask_default"
    enabled: int = 1


@admin_router.post("/pii/update")
async def update_pii(body: PiiUpdateSchema, user: dict = Depends(require_admin)):
    """更新某个 PII 字段配置。"""
    from app.clients.doris_client_manager import doris_client_manager
    from sqlalchemy import text as _text
    import app.services.access_control as ac
    doris_client_manager.init()
    try:
        async with doris_client_manager.session_factory() as s:
            await s.execute(_text(
                "DELETE FROM data_agent.pii_field_config WHERE field_id=:f"
            ), {"f": body.field_id})
            await s.execute(_text(
                "INSERT INTO data_agent.pii_field_config (field_id, mask_rule, enabled) VALUES (:f,:m,:e)"
            ), {"f": body.field_id, "m": body.mask_rule, "e": body.enabled})
            await s.commit()
        ac._PII_CACHE_LOADED = False  # force reload
        return {"status": "ok", "field_id": body.field_id,
                "mask_rule": body.mask_rule, "enabled": body.enabled}
    except Exception as e:
        logger.error(f"update_pii failed: {e}")
        return {"status": "error", "message": str(e)}



# === OPT-M10: Agent 监控看板 ===

@admin_router.get("/monitor/dashboard")
async def monitor_dashboard(user: dict = Depends(require_admin)):
    """Agent 监控看板聚合数据（文章 §7 指标）。"""
    from app.services.agent_monitor import dashboard_payload
    return await dashboard_payload()


# --------------------------------------------------------------------------- #
# Semantic layer (PRD v2.0 SS2.2): metric caliber registry + reuse check
# --------------------------------------------------------------------------- #
@admin_router.get("/metrics")
async def admin_list_metrics(user: dict = Depends(require_admin)):
    """List metric calibers (idx_metric) + bindings."""
    from app.clients.doris_client_manager import doris_client_manager
    from sqlalchemy import text
    async with doris_client_manager.session_factory() as s:
        metrics = (await s.execute(text(
            "SELECT metric_code, metric_name, aliases, biz_caliber, "
            "formula_sql, unit, time_grain, owner, status "
            "FROM data_agent.idx_metric ORDER BY metric_code LIMIT 200"
        ))).mappings().all()
        bindings = (await s.execute(text(
            "SELECT metric_code, db_name, table_name, col_expr, "
            "filter_expr, preferred FROM data_agent.idx_metric_binding LIMIT 500"
        ))).mappings().all()
        return {"metrics": [dict(r) for r in metrics],
                "bindings": [dict(r) for r in bindings]}


@admin_router.get("/metrics/reuse")
async def admin_metric_reuse(keyword: str, user: dict = Depends(require_admin)):
    """PRD SS4.3 reuse-check by name/alias keyword."""
    from app.clients.doris_client_manager import doris_client_manager
    from app.services.semantic_layer import check_metric_reuse
    async with doris_client_manager.session_factory() as s:
        return {"keyword": keyword,
                "hits": await check_metric_reuse(s, keyword)}
