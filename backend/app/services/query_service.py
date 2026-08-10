import json
import time
import asyncio
import re

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.core.audit import send_audit_log
from app.core.cache import redis_cache
from app.core.metrics import QUERY_TOTAL, QUERY_LATENCY, CACHE_HITS
from app.core.context import request_id_ctx_var
from app.core.log import logger
from app.agent.nodes.validate_sql_safety import validate_sql_safety


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



    async def get_distinct_values(self, table_name: str, column_name: str, limit: int = 100, offset: int = 0, search: str = ""):
        return await self.dw_doris_repository.get_column_values(
            table_name, column_name, limit=limit, offset=offset, search=search
        )

    async def execute_filtered_sql(self, sql: str, filters: list):
        safe_sql = str(sql).strip()
        if not safe_sql:
            raise ValueError("Empty SQL")
        ok, msg = validate_sql_safety(safe_sql)
        if not ok:
            raise ValueError(f"SQL safety check failed: {msg}")
        if filters:
            conditions = []
            params = {}
            for i, f in enumerate(filters):
                field = re.sub(r"[^a-zA-Z0-9_]", "_", str(f.get("field", "")))[:64]
                if not field:
                    continue
                op = f.get("operator", "eq")
                value = f.get("value")
                key = f"f{i}"
                safe_field = f"`{field}`"
                if op == "eq":
                    conditions.append(f"{safe_field} = :{key}")
                    params[key] = value
                elif op == "neq":
                    conditions.append(f"{safe_field} <> :{key}")
                    params[key] = value
                elif op == "contains":
                    conditions.append(f"CAST({safe_field} AS STRING) LIKE :{key}")
                    params[key] = f"%{value}%"
                elif op == "gt":
                    conditions.append(f"{safe_field} > :{key}")
                    params[key] = value
                elif op == "lt":
                    conditions.append(f"{safe_field} < :{key}")
                    params[key] = value
            if conditions:
                where_sql = " AND ".join(conditions)
                upper = safe_sql.upper()
                if " WHERE " in upper:
                    idx = upper.index(" WHERE ") + 7
                    safe_sql = safe_sql[:idx] + f"({where_sql}) AND " + safe_sql[idx:]
                elif " ORDER BY " in upper:
                    idx = upper.index(" ORDER BY ")
                    safe_sql = safe_sql[:idx] + f" WHERE {where_sql}" + safe_sql[idx:]
                else:
                    safe_sql = f"{safe_sql} WHERE {where_sql}"
        ok2, msg2 = validate_sql_safety(safe_sql)
        if not ok2:
            raise ValueError(f"SQL safety check failed after filter injection: {msg2}")
        return await self.dw_doris_repository.execute_sql(safe_sql)
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

        # P1-08: orchestrator intent routing - intercept non-data-query intents
        from app.orchestrator.query_integration import (
            ORCHESTRATOR_INTENTS, classify_intent, stream_orchestrator,
        )
        try:
            intent, _ = await classify_intent(query)
            if intent in ORCHESTRATOR_INTENTS:
                logger.info(f"Orchestrator routing: intent={intent} query={query[:30]}")
                async for line in stream_orchestrator(query, intent, username=username):
                    yield line
                return
        except Exception as e:
            logger.warning(f"Orchestrator routing failed, falling back to graph: {e}")

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
