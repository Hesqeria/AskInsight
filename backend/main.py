import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.api.routers.query_router import query_router
from app.api.routers.auth_router import auth_router
from app.api.routers.feedback_router import feedback_router
from app.api.routers.report_router import report_router
from app.api.routers.admin_router import admin_router
from app.api.routers.upload_router import upload_router
from app.api.routers.health_router import health_router
from app.api.routers.readiness_router import readiness_router
from app.clients.doris_client_manager import doris_client_manager
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.milvus_client_manager import milvus_client_manager
from app.clients.redis_client_manager import redis_client_manager
from app.core.context import request_id_ctx_var
from app.core.log import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("App startup: initializing clients")
    # C1-02: warm up jieba at startup (avoid first-request race)
    import jieba
    jieba.initialize()
    logger.info("jieba warm-up completed")
    embedding_client_manager.init()
    milvus_client_manager.init()
    doris_client_manager.init()
    try:
        redis_client_manager.init()
        logger.info("Redis connected")
    except Exception as e:
        logger.warning(f"Redis connection failed (authentication/audit unavailable): {e}")
    logger.info(f"Milvus connected: {milvus_client_manager.connected}")
    yield
    logger.info("App shutdown: releasing resources")
    milvus_client_manager.close()
    await doris_client_manager.close()
    await redis_client_manager.close()


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)
app.include_router(health_router)
app.include_router(feedback_router)
app.include_router(upload_router)
app.include_router(admin_router)
app.include_router(report_router)
app.include_router(query_router)
app.include_router(readiness_router)


@app.middleware("http")
async def set_request_id(request: Request, call_next):
    request_id_ctx_var.set(str(uuid.uuid4()))
    response = await call_next(request)
    return response
