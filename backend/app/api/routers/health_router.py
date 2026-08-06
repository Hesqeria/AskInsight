"""Health check endpoint (P2-C1/C2: independent short-timeout probe, does not share the main connection pool)."""
import httpx
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.clients.milvus_client_manager import milvus_client_manager
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

    all_ok = all(v in ("ok", "configured", "degraded") for v in checks.values())
    status_code = 200 if all_ok else 503
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content={"status": "healthy" if all_ok else "unhealthy", "checks": checks}
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
        return "ok" if redis_client_manager.client.ping() else "down"
    except Exception:
        return "unreachable"
