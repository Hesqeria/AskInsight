"""Governance router (GOV-1/2/3/5) - PRD 04-Phase4 §四."""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.clients.doris_client_manager import doris_client_manager
from app.core.auth import require_admin
from app.core.log import logger
from app.services import gov_service, gov_task_service
from fastapi.responses import JSONResponse
from pydantic import BaseModel

gov_router = APIRouter(prefix="/api/admin/gov")


@gov_router.get("/inventory")
async def get_inventory(user: dict = Depends(require_admin)):
    """GOV-1 资产盘点:dw 实际表 vs YAML 登记三清单 + 疑似改名."""
    async with doris_client_manager.session_factory() as session:
        return await gov_service.asset_inventory(session)


@gov_router.get("/compliance")
async def get_compliance(user: dict = Depends(require_admin)):
    """GOV-2 落标报告:注释率/口径率/glossary 健康 + 红绿灯."""
    async with doris_client_manager.session_factory() as session:
        return await gov_service.compliance_report(session)


@gov_router.post("/quality/run")
async def post_quality_run(body: dict = None,
                           user: dict = Depends(require_admin)):
    """GOV-3 质量检查:对 ads 表生成并执行规则,结果落库(只记不修)."""
    body = body or {}
    tables = body.get("tables") or None
    async with doris_client_manager.session_factory() as session:
        return await gov_service.run_quality_checks(session, tables)


@gov_router.get("/quality/latest-failures")
async def get_quality_failures(user: dict = Depends(require_admin)):
    """GOV-3: failures of the most recent run (for dashboard table)."""
    async with doris_client_manager.session_factory() as session:
        rows = (await session.execute(text(
            "SELECT table_name, rule_type, metric_value, detail "
            "FROM data_agent.gov_quality_result "
            "WHERE passed = FALSE AND run_id = "
            "(SELECT MAX(run_id) FROM data_agent.gov_quality_result) "
            "ORDER BY table_name, rule_type"))).fetchall()
        return {"failures": [
            {"table_name": r[0], "rule_type": r[1],
             "metric_value": float(r[2]) if r[2] is not None else None,
             "detail": r[3] or ""} for r in rows]}


@gov_router.get("/overview")
async def get_overview(user: dict = Depends(require_admin)):
    """GOV-5 治理大盘:资产/落标/质量/成本四视图聚合."""
    async with doris_client_manager.session_factory() as session:
        return await gov_service.governance_overview(session)


@gov_router.get("/quality-metrics")
async def get_quality_metrics(days: int = 30, user: dict = Depends(require_admin)):
    """PRD 实施版 §6 五项质量指标(基于 sql_candidate / session_event / clarify_session)."""
    doris_client_manager.init()
    try:
        async with doris_client_manager.session_factory() as session:
            # ①+② 一次准确率与候选一致性
            r = (await session.execute(text(
                "SELECT COUNT(DISTINCT CASE WHEN chosen AND cand_no = 1 THEN request_id END), "
                "COUNT(DISTINCT request_id) FROM data_agent.sql_candidate "
                "WHERE created_at >= DATE_SUB(NOW(), INTERVAL :d DAY)"),
                {"d": days})).fetchone()
            hit, total = int(r[0] or 0), int(r[1] or 0)
            first_hit_pct = round(hit / total * 100, 1) if total else None
            dist = (await session.execute(text(
                "SELECT chosen_by, COUNT(*) FROM data_agent.sql_candidate "
                "WHERE chosen AND created_at >= DATE_SUB(NOW(), INTERVAL :d DAY) GROUP BY chosen_by"),
                {"d": days})).fetchall()
            # ③+⑤ 人工介入率与慢请求(基于 turn/ended payload)
            turns = (await session.execute(text(
                "SELECT get_json_string(payload, '$.reason') AS reason, "
                "get_json_double(payload, '$.latency_ms') AS ms "
                "FROM data_agent.session_event WHERE type = 'turn/ended' "
                "AND created_at >= DATE_SUB(NOW(), INTERVAL :d DAY)"),
                {"d": days})).fetchall()
            reasons = {}
            lat = []
            for reason, ms in turns:
                reasons[reason or "unknown"] = reasons.get(reason or "unknown", 0) + 1
                if ms:
                    lat.append(float(ms))
            n_turns = len(turns)
            ok = reasons.get("success", 0)
            human = n_turns - ok
            lat_sorted = sorted(lat)
            slow_over_60s = sum(1 for v in lat if v > 60000)
            # ④ 澄清轮次
            cl = (await session.execute(text(
                "SELECT AVG(rounds), COUNT(*) FROM data_agent.clarify_session "
                "WHERE created_at >= DATE_SUB(NOW(), INTERVAL :d DAY)"),
                {"d": days})).fetchone()
            return {
                "window_days": days,
                "first_hit_pct": first_hit_pct,
                "first_hit": hit,
                "requests_with_candidates": total,
                "chosen_by_distribution": {str(k): int(v) for k, v in dist},
                "human_intervention_rate_pct": round(human / n_turns * 100, 1) if n_turns else None,
                "turn_total": n_turns,
                "turn_reasons": reasons,
                "clarify_avg_rounds": round(float(cl[0]), 2) if cl and cl[0] is not None else None,
                "clarify_sessions": int(cl[1] or 0),
                "latency": {
                    "count": len(lat),
                    "avg_ms": round(sum(lat) / len(lat)) if lat else None,
                    "p90_ms": lat_sorted[int(len(lat_sorted) * 0.9) - 1] if lat_sorted else None,
                    "over_60s": slow_over_60s,
                },
            }
    except Exception as e:
        logger.error(f"quality-metrics failed: {e}")
        raise


@gov_router.get("/tasks")
async def list_gov_tasks(user: dict = Depends(require_admin)):
    """P5: list governance tasks with due status."""
    doris_client_manager.init()
    async with doris_client_manager.session_factory() as session:
        return {"status": "ok", "tasks": await gov_task_service.list_tasks(session)}


class GovTaskBody(BaseModel):
    task_id: str
    task_type: str = "quality_check"
    interval_minutes: int = 1440


@gov_router.post("/tasks")
async def create_gov_task(body: GovTaskBody, user: dict = Depends(require_admin)):
    doris_client_manager.init()
    async with doris_client_manager.session_factory() as session:
        result = await gov_task_service.create_task(
            session, body.task_id, body.task_type, body.interval_minutes)
    if not result.get("ok"):
        return JSONResponse(status_code=400, content={"status": "error", **result})
    return {"status": "ok", **result}


@gov_router.post("/tasks/{task_id}/run")
async def run_gov_task(task_id: str, user: dict = Depends(require_admin)):
    doris_client_manager.init()
    async with doris_client_manager.session_factory() as session:
        result = await gov_task_service.run_task_now(session, task_id)
    if not result.get("ok"):
        return JSONResponse(status_code=404, content={"status": "error", **result})
    return {"status": "ok", **result}


@gov_router.post("/tasks/{task_id}/toggle")
async def toggle_gov_task(task_id: str, user: dict = Depends(require_admin)):
    doris_client_manager.init()
    async with doris_client_manager.session_factory() as session:
        result = await gov_task_service.toggle_task(session, task_id)
    if not result.get("ok"):
        return JSONResponse(status_code=404, content={"status": "error", **result})
    return {"status": "ok", **result}


@gov_router.get("/exemplars")
async def list_exemplars(limit: int = 50, user: dict = Depends(require_admin)):
    """Vanna-style exemplar store inspection."""
    doris_client_manager.init()
    async with doris_client_manager.session_factory() as session:
        rows = (await session.execute(text(
            "SELECT exemplar_id, question, source, request_id, created_at "
            "FROM data_agent.nl2sql_exemplar ORDER BY created_at DESC LIMIT :l"),
            {"l": max(1, min(int(limit), 200))})).fetchall()
        total = (await session.execute(text(
            "SELECT COUNT(*) FROM data_agent.nl2sql_exemplar"))).scalar()
    return {"status": "ok", "total": int(total or 0),
            "exemplars": [{"exemplar_id": r[0], "question": r[1], "source": r[2],
                            "request_id": r[3], "created_at": str(r[4])} for r in rows]}


@gov_router.post("/exemplars/seed")
async def seed_exemplars(user: dict = Depends(require_admin)):
    doris_client_manager.init()
    async with doris_client_manager.session_factory() as session:
        added = await gov_task_service_exemplar_seed(session)
    return {"status": "ok", "added": added}


async def gov_task_service_exemplar_seed(session):
    from app.services import exemplar_store
    return await exemplar_store.seed_from_feedback(session)
