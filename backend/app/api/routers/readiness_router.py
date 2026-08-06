"""准入评分 API

GET  /api/readiness        - 获取评分报告
GET  /api/readiness/gate   - 检查是否通过门槛（查询前置检查）
"""
import yaml
from fastapi import APIRouter, Depends

from app.agent.nodes.validate_sql_safety import ALLOWED_TABLES
from app.core.auth import verify_token
from app.core.log import logger

readiness_router = APIRouter()

# 缓存
_cached_report = None


def _load_table_infos() -> list[dict]:
    """加载 meta_config 中的表信息"""
    from pathlib import Path
    config_path = Path(__file__).parents[3] / "conf" / "meta_config_dw.yaml"
    if not config_path.exists():
        config_path = Path(__file__).parents[3] / "conf" / "meta_config.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("tables", [])


@readiness_router.get("/api/readiness")
async def get_readiness(user: dict = Depends(verify_token)):
    """获取项目准入评分报告"""
    global _cached_report
    if _cached_report:
        return _cached_report

    from app.scripts.readiness_check import check_readiness, format_report_text

    table_infos = _load_table_infos()
    report = await check_readiness(
        meta_repo=None,
        table_infos=table_infos,
        allowed_tables=ALLOWED_TABLES,
    )

    result = {
        "total_score": report.total_score,
        "passed": report.passed,
        "threshold": 70.0,
        "summary": report.summary,
        "dimensions": [
            {
                "name": d.name,
                "score": d.score,
                "weight": d.weight,
                "threshold": d.threshold,
                "passed": d.passed,
                "detail": d.detail,
            }
            for d in report.dimensions
        ],
        "recommendations": report.recommendations,
    }
    _cached_report = result
    logger.info(f"准入评分请求: {report.summary}")
    return result


@readiness_router.get("/api/readiness/gate")
async def readiness_gate(user: dict = Depends(verify_token)):
    """准入门禁检查（查询前置）

    返回 passed=True 才允许执行 NL2SQL 查询。
    """
    global _cached_report
    if not _cached_report:
        await get_readiness(user)
    return {
        "passed": _cached_report["passed"],
        "score": _cached_report["total_score"],
        "message": _cached_report["summary"],
    }


@readiness_router.post("/api/readiness/refresh")
async def refresh_readiness(user: dict = Depends(verify_token)):
    """刷新评分缓存（元数据更新后调用）"""
    global _cached_report
    _cached_report = None
    return {"message": "缓存已清除，下次请求将重新评分"}

@readiness_router.post("/api/readiness/agent")
async def run_agent_smoke_test(user: dict = Depends(verify_token)):
    """Run agent end-to-end smoke test — DB + Schema + Query + Embedding + LLM + Milvus."""
    from app.scripts.agent_readiness import AgentSmokeTest
    from app.clients.doris_client_manager import doris_client_manager
    from app.clients.embedding_client_manager import embedding_client_manager
    from app.clients.milvus_client_manager import milvus_client_manager
    from app.agent.llm import llm
    
    tester = AgentSmokeTest(
        session_factory=doris_client_manager.session_factory,
        embedding_client=embedding_client_manager,
        llm_client=llm,
        milvus_client=milvus_client_manager,
    )
    results = await tester.run_all()
    score = tester.compute_score(results)
    
    return {
        "score": score,
        "passed": score == 100,
        "tests": [
            {
                "name": r.name,
                "passed": r.passed,
                "elapsed_ms": r.elapsed_ms,
                "detail": r.detail,
                "error": r.error,
            }
            for r in results
        ]
    }
