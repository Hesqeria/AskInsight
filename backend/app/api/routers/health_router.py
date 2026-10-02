"""Health check endpoint (P2-C1/C2: independent short-timeout probe, does not share the main connection pool)."""
import httpx
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.clients.milvus_client_manager import milvus_client_manager
from app.clients.rerank_client_manager import rerank_client_manager
from app.clients.redis_client_manager import redis_client_manager
from app.conf.app_config import app_config

health_router = APIRouter()


@health_router.get("/health")
async def health():
    """P2-C3: tiered check, returns 503 if any component fails."""
    checks = {}

    # 1. Doris (independent short timeout, does not share the connection pool)
    checks["doris"] = await _check_doris()
    from app.clients.doris_client_manager import doris_client_manager
    if doris_client_manager and doris_client_manager.engine:
        pool = doris_client_manager.engine.pool
        checks["doris_pool"] = f"size={pool.size()}"

    # 2. Milvus (use the connected flag, no actual request sent)
    checks["milvus"] = "ok" if milvus_client_manager.connected else "degraded"

    # 3. Redis (ping, 1s timeout)
    checks["redis"] = await _check_redis()

    # 4. Embedding (no request, only config check)
    checks["embedding"] = "configured" if app_config.embedding.api_key else "missing"

    # 5. LLM (config check only, no request sent, P2-C3)
    checks["llm"] = "configured" if app_config.llm.api_key else "missing"

    # 6. Rerank (optional component; config check only here, live probe
    # lives at /health/rerank so a slow dashscope call doesn't drag the
    # main health check).
    checks["rerank"] = (
        "configured" if rerank_client_manager.is_available()
        else "disabled"
    )

    healthy_states = {"ok", "configured", "degraded", "disabled"}
    status_checks = {k: v for k, v in checks.items() if not k.endswith("_pool")}
    has_critical = any(v not in healthy_states for v in status_checks.values())
    status_code = 503 if has_critical else 200
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content={"status": "unhealthy" if has_critical else "healthy", "checks": checks}
    )


@health_router.get("/health/rerank")
async def health_rerank():
    """Live probe for the rerank service. Sends a tiny 1-doc call to
    Bailian to verify api_key + model + network. Returns 200 on success,
    503 on any failure (with the error message for debugging)."""
    from fastapi.responses import JSONResponse
    if not rerank_client_manager.is_available():
        return JSONResponse(
            status_code=200,
            content={"status": "disabled",
                     "note": "rerank not configured; pipeline falls back to RRF"},
        )
    try:
        out = await rerank_client_manager.client.arerank(
            "health check",
            ["alive probe document"],
            top_n=1,
        )
        ok = bool(out) and "relevance_score" in out[0]
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "status": "ok" if ok else "degraded",
                "model": app_config.rerank.model,
                "score": out[0]["relevance_score"] if ok else None,
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "error": str(e)},
        )


@health_router.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    """Prometheus metrics endpoint."""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def _check_doris() -> str:
    """Independent short-timeout Doris check (P2-C1: does not share the main connection pool)."""
    try:
        url = f"http://{app_config.doris.host}:{app_config.doris.port + 5000}/api/health"
        # Doris FE HTTP port = MySQL port + 5000 (9030->14030 is wrong, use fe_http_port instead)
        # Actually use Doris FE Web port 8030
        fe_url = f"http://{app_config.doris.host}:8030/api/show_proc?path=/frontends"
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(fe_url)
            return "ok" if r.status_code == 200 else f"http_{r.status_code}"
    except Exception:
        return "unreachable"


async def _check_redis() -> str:
    try:
        pong = await redis_client_manager.client.ping()
        return "ok" if pong else "down"
    except Exception:
        return "down"
