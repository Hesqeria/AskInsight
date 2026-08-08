import json
import time
import asyncio

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.core.audit import send_audit_log
from app.core.cache import redis_cache
from app.core.metrics import QUERY_TOTAL, QUERY_LATENCY, CACHE_HITS
from app.core.context import request_id_ctx_var
from app.core.log import logger


class QueryService:
    def __init__(self,
                 embedding_client,
                 column_milvus_repository,
                 metric_milvus_repository,
                 value_doris_repository,
                 meta_doris_repository,
                 dw_doris_repository):
        self.embedding_client = embedding_client
        self.column_milvus_repository = column_milvus_repository
        self.metric_milvus_repository = metric_milvus_repository
        self.value_doris_repository = value_doris_repository
        self.meta_doris_repository = meta_doris_repository
        self.dw_doris_repository = dw_doris_repository

    async def query(self, query: str, history: list = None, username: str = "anonymous"):
        start_time = time.time()
        request_id = request_id_ctx_var.get()
        import hashlib
        last_history = history[-1] if history else ""
        cache_key = hashlib.md5(f"{query}|{last_history}".encode()).hexdigest()
        cached = await redis_cache.get(cache_key)
        if cached:
            logger.info(f"Cache hit: {query[:30]}")
            CACHE_HITS.inc()
            await send_audit_log(request_id, username, query, status="cache_hit",
                                 latency_ms=int((time.time() - start_time) * 1000))
            for line in cached:
                yield line
            return

        context = DataAgentContext(
            embedding_client=self.embedding_client,
            column_milvus_repository=self.column_milvus_repository,
            metric_milvus_repository=self.metric_milvus_repository,
            value_doris_repository=self.value_doris_repository,
            meta_doris_repository=self.meta_doris_repository,
            dw_doris_repository=self.dw_doris_repository,
        )
        state = DataAgentState(query=query, error=None, history=history or [])
        sse_lines = []
        final_sql = ""
        result_rows = 0
        status = "success"
        try:
            async for chunk in graph.astream(
                input=state, context=context, stream_mode="custom"
            ):
                line = 'data: ' + json.dumps(chunk, ensure_ascii=False, default=str) + '\n\n'
                yield line
                sse_lines.append(line)
                if isinstance(chunk, dict):
                    if chunk.get("error"):
                        status = "error"
                    if isinstance(chunk.get("result"), list):
                        result_rows = len(chunk["result"])
            await redis_cache.set(cache_key, sse_lines)
        except asyncio.CancelledError:
            # Client disconnected - silent exit, no error log
            logger.info(f"Client disconnected (request_id={request_id})")
            status = "cancelled"
            raise
        except Exception as e:
            logger.error(f"QueryService exception: {e}", exc_info=True)
            status = "error"
            err_line = 'data: ' + json.dumps({"error": str(e)}, ensure_ascii=False, default=str) + '\n\n'
            yield err_line
        finally:
            latency = int((time.time() - start_time) * 1000)
            QUERY_TOTAL.labels(status=status).inc()
            QUERY_LATENCY.labels(status=status).observe(latency / 1000.0)
            await send_audit_log(request_id, username, query, sql=final_sql,
                                 status=status, latency_ms=latency, result_rows=result_rows)
