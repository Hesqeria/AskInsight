import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.api.routers.query_router import query_router
from app.api.routers.auth_router import auth_router
from app.api.routers.kpi_router import kpi_router
from app.api.routers.feedback_router import feedback_router
from app.api.routers.report_router import report_router
from app.api.routers.admin_router import admin_router
from app.api.routers.upload_router import upload_router
from app.api.routers.health_router import health_router
from app.api.routers.readiness_router import readiness_router
from app.api.routers.quality_router import quality_router
from app.api.routers.gov_router import gov_router
from app.api.routers.modeling_router import modeling_router
from app.api.routers.docs_router import docs_router
from app.api.routers.model_router import model_router
from app.api.routers.lineage_router import lineage_router
from app.api.routers.metadata_router import metadata_router
from app.api.routers.alerts_router import alerts_router
from app.api.routers.orchestrator_router import orchestrator_router
from app.api.routers.rl_router import rl_router
from app.api.routers.ontology_router import ontology_router
from app.api.routers.approval_router import approval_router
from app.api.routers.clarify_router import clarify_router
from app.api.routers.session_router import session_router
from app.api.routers.inbox_router import inbox_router
from app.api.routers.spill_router import spill_router
from app.clients.doris_client_manager import doris_client_manager
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.milvus_client_manager import milvus_client_manager
from app.clients.redis_client_manager import redis_client_manager
from app.clients.rerank_client_manager import rerank_client_manager
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
    # Rerank is optional; init is safe even without an api_key (it just
    # flips `enabled` off and the merge node falls back to RRF ordering).
    try:
        rerank_client_manager.init()
    except Exception as e:
        logger.warning(f"Rerank init failed (will use pure RRF ordering): {e}")
    milvus_client_manager.init()
    doris_client_manager.init()
    try:
        redis_client_manager.init()
        logger.info("Redis connected")
    except Exception as e:
        logger.warning(f"Redis connection failed (authentication/audit unavailable): {e}")
    logger.info(f"Milvus connected: {milvus_client_manager.connected}")

    # Cold-start warmup: first-query latency used to include graph
    # compile (31 node imports), first Doris pool checkout, and first
    # LLM/embedding roundtrips (~65-100s on a cold box). Fire-and-
    # forget so the API is up immediately.
    async def _warmup():
        import time as _t
        t0 = _t.time()
        async def _embed_ping():
            try:
                await embedding_client_manager.client.aembed_query("warmup")
                return "embedding ok"
            except Exception as e:
                return f"embedding skip: {str(e)[:60]}"
        async def _doris_ping():
            try:
                async with doris_client_manager.session_factory() as s:
                    from sqlalchemy import text as _text
                    await s.execute(_text("SELECT 1"))
                return "doris ok"
            except Exception as e:
                return f"doris skip: {str(e)[:60]}"
        async def _llm_ping():
            try:
                from app.agent.llm import llm as _llm, fast_llm as _fast
                await _llm.ainvoke("ping")
                await _fast.ainvoke("ping")
                return "llm ok"
            except Exception as e:
                return f"llm skip: {str(e)[:60]}"
        async def _semantic_seed():
            try:
                from app.services.semantic_layer import ensure_seeded
                async with doris_client_manager.session_factory() as s2:
                    await ensure_seeded(s2)
                return "semantic seed ok"
            except Exception as e:
                return f"semantic skip: {str(e)[:60]}"
        async def _audit_ping():
            try:
                from app.core.audit import _get_producer
                _get_producer()
                return "audit probe ok"
            except Exception as e:
                return f"audit skip: {str(e)[:60]}"
        import asyncio as _a
        results = await _a.gather(
            _embed_ping(), _doris_ping(), _llm_ping(), _audit_ping(),
            _semantic_seed(),
            return_exceptions=True)
        try:
            from app.agent.graph import graph  # noqa: F401 (builds 31-node graph)
        except Exception as e:
            results = list(results) + [f"graph skip: {str(e)[:60]}"]
        logger.info(f"warmup done in {_t.time()-t0:.1f}s: {results}")

    import asyncio as _warm_aio
    _warm_aio.create_task(_warmup())
    # M7: approval expiry sweeper - pending tickets older than
    # approval.expire_hours become expired; matching inbox items are
    # discarded (fail-closed) so nothing waits forever.
    import asyncio as _asyncio
    import os as _os

    async def _approval_sweeper():
        while True:
            try:
                await _asyncio.sleep(3600)
                from app.conf.app_config import app_config
                hours = app_config.approval.expire_hours
                if not hours or hours <= 0:
                    continue
                from app.clients.doris_client_manager import doris_client_manager
                from app.services.approval_policy import (
                    expire_pending, emit_decided_event,
                )
                async with doris_client_manager.session_factory() as s:
                    tids = await expire_pending(s, hours)
                for tid in tids:
                    emit_decided_event(tid, "expired", "system:sweeper",
                                       "expired by policy sweeper")
                    try:
                        from app.repositories.doris.inbox.inbox_repository import (
                            InboxRepository,
                        )
                        async with doris_client_manager.session_factory() as s2:
                            inbox_repo = InboxRepository(s2)
                            for item in await inbox_repo.list_pending():
                                if (item.payload or {}).get("ref_id") == tid:
                                    await inbox_repo.mark(item.inbox_id,
                                                           "discarded")
                    except Exception as inner:
                        logger.debug(f"inbox discard for {tid} failed: {inner}")
                if tids:
                    logger.info(f"approval sweeper expired {len(tids)} ticket(s)")
                # M10: spill TTL sweep (7-day retention by default).
                try:
                    from app.services.spill_store import delete_expired
                    async with doris_client_manager.session_factory() as s3:
                        n = await delete_expired(s3)
                    if n:
                        logger.info(f"spill sweeper deleted {n} expired row(s)")
                except Exception as _se:
                    logger.debug(f"spill sweep failed: {_se}")
            except _asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"approval sweeper tick failed: {e}")

    _sweeper_task = _asyncio.create_task(_approval_sweeper())

    async def _gov_task_loop():
        """P5: periodic governance task sweep (quality checks etc.)."""
        interval = int(_os.getenv("GOV_TASK_LOOP_SECONDS", "300"))
        await _asyncio.sleep(15)  # let warmup finish first
        while True:
            try:
                from app.services import gov_task_service
                async with doris_client_manager.session_factory() as s:
                    await gov_task_service.seed_tasks(s)
                    await gov_task_service.run_due_tasks(s)
            except _asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"gov task loop tick failed: {e}")
            await _asyncio.sleep(interval)

    _gov_task_loop_task = _asyncio.create_task(_gov_task_loop())

    async def _seed_embed_cache():
        """Warm the embedding cache with glossary/registry vocabulary so
        first-turn queries skip throttled per-text embedding calls."""
        await _asyncio.sleep(20)
        try:
            from sqlalchemy import text as _text
            from app.core.embed_cache import seed_many
            texts = set()
            from app.agent.nodes.semantic_grounding import (
                MEASURE_REGISTRY, _BUSINESS_TERM_ALIASES)
            texts.update(k for k in MEASURE_REGISTRY if k)
            for m in MEASURE_REGISTRY.values():
                if isinstance(m, dict):
                    texts.add(m.get("column", "") or "")
            texts.update(a for a in _BUSINESS_TERM_ALIASES if a)
            texts.update(v for v in _BUSINESS_TERM_ALIASES.values() if v)
            async with doris_client_manager.session_factory() as s:
                rows = (await s.execute(_text(
                    "SELECT term, standard_name FROM data_agent.glossary "
                    "WHERE status = 1"))).fetchall()
                for term, std in rows:
                    if term:
                        texts.add(term)
                    if std:
                        texts.add(std)
            texts.discard("")
            logger.info(f"seeding embed cache with {min(len(texts), 120)} texts...")
            await seed_many(embedding_client_manager.client, sorted(texts)[:120])
        except _asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"embed cache seed failed: {e}")

    _embed_seed_task = _asyncio.create_task(_seed_embed_cache())

    async def _seed_exemplars():
        """Vanna-style flywheel: load verified question-SQL pairs
        (user corrections + successful executions) at startup, then
        pre-embed their questions so retrieval never pays cold cost."""
        try:
            from sqlalchemy import text as _t2
            from app.services import exemplar_store
            from app.core.embed_cache import seed_many
            async with doris_client_manager.session_factory() as s:
                await exemplar_store.seed_from_feedback(s)
                rows = (await s.execute(_t2(
                    "SELECT question FROM data_agent.nl2sql_exemplar "
                    "LIMIT 200"))).fetchall()
            qs = [r[0] for r in rows if r[0]]
            if qs:
                await seed_many(embedding_client_manager.client, qs)
                logger.info(f"exemplar questions pre-embedded: {len(qs)}")
        except Exception as e:
            logger.warning(f"exemplar seed failed: {e}")

    try:
        _asyncio.get_running_loop().create_task(_seed_exemplars())
    except RuntimeError:
        pass
    yield
    _sweeper_task.cancel()
    _gov_task_loop_task.cancel()
    _embed_seed_task.cancel()
    logger.info("App shutdown: releasing resources")
    milvus_client_manager.close()
    await doris_client_manager.close()
    await redis_client_manager.close()


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)
app.include_router(kpi_router)
app.include_router(health_router)
app.include_router(feedback_router)
app.include_router(upload_router)
app.include_router(admin_router)
app.include_router(report_router)
app.include_router(query_router)
app.include_router(readiness_router)
app.include_router(quality_router)
app.include_router(gov_router)
app.include_router(modeling_router)
app.include_router(docs_router)
app.include_router(model_router)
app.include_router(lineage_router)
app.include_router(metadata_router)
app.include_router(alerts_router)
app.include_router(orchestrator_router)
app.include_router(rl_router)
app.include_router(ontology_router)
app.include_router(approval_router)
app.include_router(clarify_router)
app.include_router(session_router)
app.include_router(inbox_router)
app.include_router(spill_router)


@app.middleware("http")
async def set_request_id(request: Request, call_next):
    request_id_ctx_var.set(str(uuid.uuid4()))
    response = await call_next(request)
    return response
