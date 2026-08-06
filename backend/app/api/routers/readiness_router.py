"""Readiness scoring API.

GET  /api/readiness        - get the score report
GET  /api/readiness/gate   - check whether the threshold is passed (pre-query check)
"""
import yaml
from fastapi import APIRouter, Depends

from app.agent.nodes.validate_sql_safety import ALLOWED_TABLES
from app.core.auth import verify_token
from app.core.log import logger

readiness_router = APIRouter()

# Cache
_cached_report = None


def _load_table_infos() -> list[dict]:
    """Load table info from meta_config."""
    from pathlib import Path
    config_path = Path(__file__).parents[3] / "conf" / "meta_config_dw.yaml"
    if not config_path.exists():
        config_path = Path(__file__).parents[3] / "conf" / "meta_config.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("tables", [])


@readiness_router.get("/api/readiness")
async def get_readiness(user: dict = Depends(verify_token)):
    """Get the project readiness score report."""
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
    logger.info(f"Readiness scoring request: {report.summary}")
    return result


@readiness_router.get("/api/readiness/gate")
async def readiness_gate(user: dict = Depends(verify_token)):
    """Readiness gate check (pre-query).

    Only when passed=True is the NL2SQL query allowed to execute.
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
    """Refresh the score cache (call after metadata updates)."""
    global _cached_report
    _cached_report = None
    return {"message": "Cache cleared; the next request will re-score"}
