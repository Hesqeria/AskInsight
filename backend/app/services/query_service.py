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


def _find_keyword_outside_strings(sql: str, keyword: str) -> int:
    """Return the index of the first occurrence of `keyword` (matched
    case-insensitively, surrounded by the spaces already in `keyword`)
    that is NOT inside a single-quoted SQL string literal. Returns -1 if
    none is found. Handles embedded '' escapes inside the literal."""
    needle = keyword.upper()
    haystack = sql.upper()
    n = len(haystack)
    m = len(needle)
    in_str = False
    i = 0
    while i < n:
        ch = sql[i]
        if ch == "'":
            if in_str and i + 1 < n and sql[i + 1] == "'":
                # Escaped quote inside the literal - skip both chars.
                i += 2
                continue
            in_str = not in_str
            i += 1
            continue
        if not in_str and haystack[i:i + m] == needle:
            return i
        i += 1
    return -1


class QueryService:
    def __init__(self,
                 embedding_client,
                 column_milvus_repository,
                 metric_milvus_repository,
                 value_doris_repository,
                 meta_doris_repository,
                 dw_doris_repository,
                 rerank_client=None,
                 rl_repository=None):
        self.embedding_client = embedding_client
        self.column_milvus_repository = column_milvus_repository
        self.metric_milvus_repository = metric_milvus_repository
        self.value_doris_repository = value_doris_repository
        self.meta_doris_repository = meta_doris_repository
        self.dw_doris_repository = dw_doris_repository
        self.rerank_client = rerank_client
        self.rl_repository = rl_repository



    async def get_distinct_values(self, table_name: str, column_name: str, limit: int = 100, offset: int = 0, search: str = ""):
        return await self.dw_doris_repository.get_column_values(
            table_name, column_name, limit=limit, offset=offset, search=search
        )

    async def execute_filtered_sql(self, sql: str, filters: list):
        # Normalize first: a trailing ";" before appended clauses is a
        # syntax error (WrenAI #2740 class of bugs).
        safe_sql = str(sql).strip().rstrip(";").strip()
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
                # CTE-leading SQL: the first WHERE may live inside a CTE
                # definition (SuperSonic #2427 class) - wrap instead of
                # injecting so the filter always scopes the final result.
                is_cte = safe_sql[:5].upper() == "WITH "
                if is_cte:
                    safe_sql = (f"SELECT * FROM ({safe_sql}) __flt "
                                f"WHERE {where_sql}")
                else:
                    # Find clause boundaries OUTSIDE string literals. A naive
                    # `upper.index(" WHERE ")` previously matched inside
                    # literals such as `WHERE note = ' WHERE ignored'`,
                    # producing malformed injected SQL.
                    where_idx = _find_keyword_outside_strings(safe_sql, " WHERE ")
                    if where_idx != -1:
                        insert_at = where_idx + 7
                        safe_sql = (safe_sql[:insert_at]
                                    + f"({where_sql}) AND "
                                    + safe_sql[insert_at:])
                    else:
                        order_idx = _find_keyword_outside_strings(
                            safe_sql, " ORDER BY ")
                        if order_idx != -1:
                            safe_sql = (safe_sql[:order_idx]
                                        + f" WHERE {where_sql}"
                                        + safe_sql[order_idx:])
                        else:
                            # No WHERE/ORDER BY: never append after a
                            # top-level LIMIT - insert BEFORE it.
                            limit_idx = _find_toplevel_limit_idx(safe_sql)
                            if limit_idx != -1:
                                safe_sql = (safe_sql[:limit_idx]
                                            + f"WHERE {where_sql} "
                                            + safe_sql[limit_idx:])
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

        # OPT-M9: result-level cache (24h for stable past-date queries).
        try:
            from app.services.result_cache import get_cached_result
            rc = await get_cached_result(query, username)
            if rc:
                logger.info(f"OPT-M9 result cache HIT: {query[:30]}")
                CACHE_HITS.inc()
                await send_audit_log(request_id, username, query, sql=rc.get("sql", ""),
                                     status="result_cache_hit",
                                     latency_ms=int((time.time() - start_time) * 1000))
                sql_line = "data: " + json.dumps({"sql": rc.get("sql", "")}, ensure_ascii=False, default=str) + chr(10)+chr(10)
                res_payload = {"result": rc.get("result", [])}
                # Replay may predate the summary feature - synthesize a
                # minimal one for single-value rows from the column label.
                rows_ = rc.get("result") or []
                if (len(rows_) == 1 and isinstance(rows_[0], dict)
                        and 1 <= len(rows_[0]) <= 2
                        and "hint" not in rows_[0]):
                    def _is_num_v(v):
                        if isinstance(v, (int, float)):
                            return True
                        try:
                            float(str(v).replace(',', ''))
                            return True
                        except (TypeError, ValueError):
                            return False
                    cols_ = list(rows_[0].keys())
                    num_col = next((c for c in cols_ if _is_num_v(rows_[0][c])), None)
                    cat_col = next((c for c in cols_ if c != num_col), None)
                    if num_col is None and cat_col is None:
                        num_col = cols_[0]
                    if num_col and rows_[0].get(num_col) is None:
                        res_payload["summary"] = {
                            "metric": num_col, "value": None, "unit": "",
                            "text": f"{num_col}无数据"}
                    elif num_col:
                        try:
                            fv = float(str(rows_[0][num_col]).replace(',', ''))
                            label = cat_col or ""
                            text = (f"最高：{rows_[0].get(cat_col)}（{fv:,.2f}）"
                                    if label else
                                    (f"{num_col}为 {fv:,.2f} 元"
                                     if any(w in num_col for w in ("GMV", "金额", "销售额", "客单价"))
                                     else f"{num_col}为 {fv:,.0f}"))
                            res_payload["summary"] = {
                                "metric": num_col, "value": fv, "unit": "",
                                "text": text}
                        except (TypeError, ValueError):
                            pass
                res_line = "data: " + json.dumps(res_payload, ensure_ascii=False, default=str) + chr(10)+chr(10)
                yield sql_line
                yield res_line
                return
        except Exception as e:
            logger.debug(f"OPT-M9 result cache get skipped: {e}")

        cached = await redis_cache.get(cache_key)
        if cached:
            logger.info(f"Cache hit: {query[:30]}")
            CACHE_HITS.inc()
            await send_audit_log(request_id, username, query, status="cache_hit",
                                 latency_ms=int((time.time() - start_time) * 1000))
            for line in cached:
                yield line
            return

        # P1-08: orchestrator intent routing - intercept non-data-query intents.
        from app.orchestrator.query_integration import (
            ORCHESTRATOR_INTENTS, classify_intent, stream_orchestrator,
        )
        orch_status = "success"
        orch_start = time.time()
        try:
            intent, source = await classify_intent(query)
            # Per-query routing log so misroutes are observable.
            logger.info(f"Intent routing: intent={intent} source={source} "
                        f"query={query[:60]!r}")
            if intent in ORCHESTRATOR_INTENTS:
                try:
                    async for line in stream_orchestrator(query, intent, username=username):
                        if '"error"' in line:
                            orch_status = "error"
                        yield line
                finally:
                    # Record metrics for the orchestrator path (previously
                    # skipped because of the early return). Audit logging
                    # still happens inside stream_orchestrator's own finally.
                    latency = int((time.time() - orch_start) * 1000)
                    QUERY_TOTAL.labels(status=orch_status).inc()
                    QUERY_LATENCY.labels(status=orch_status).observe(latency / 1000.0)
                return
        except Exception as e:
            logger.warning(f"Orchestrator routing failed, falling back to graph: {e}")

        context = DataAgentContext(
            embedding_client=self.embedding_client,
            rerank_client=self.rerank_client,
            rl_repository=self.rl_repository,
            column_milvus_repository=self.column_milvus_repository,
            metric_milvus_repository=self.metric_milvus_repository,
            value_doris_repository=self.value_doris_repository,
            meta_doris_repository=self.meta_doris_repository,
            dw_doris_repository=self.dw_doris_repository,
        )
        user_role = "user"
        try:
            from app.core.auth import USERS
            user_role = (USERS.get(username) or {}).get("role", "user")
        except Exception:
            pass
        state = DataAgentState(
            query=query, error=None, history=history or [],
            _username=username,
            _role=user_role,
            _request_id=request_id,
        )
        sse_lines = []
        final_sql = ""
        result_rows = 0
        status = "success"
        # Surface the session id (= request id) so the frontend can
        # open the M1 event timeline for this turn.
        yield 'data: ' + json.dumps(
            {'request_id': request_id}, ensure_ascii=False,
        ) + chr(10) + chr(10)
        # M1: query/received opens the session event timeline.
        from app.agent.events import emit
        emit("query/received", {"query": query[:300], "username": username,
                                "history_turns": len(history or [])})
        # Fold node patches (stream_mode="updates") into a slim state
        # snapshot used for the state/checkpoint event (M3 resume source).
        _state_fold: dict = dict(state)
        _WAIT_KEYS = ("pending_clarify_id", "pending_approval_ticket_id")
        try:
            async for mode, chunk in graph.astream(
                input=state, context=context, stream_mode=["custom", "updates"]
            ):
                if mode == "updates":
                    for _node, patch in (chunk or {}).items():
                        if isinstance(patch, dict):
                            _state_fold.update(patch)
                    continue
                line = 'data: ' + json.dumps(chunk, ensure_ascii=False, default=str) + '\n\n'
                yield line
                sse_lines.append(line)
                if isinstance(chunk, dict):
                    if chunk.get("error"):
                        status = "error"
                    if isinstance(chunk.get("sql"), str):
                        final_sql = chunk["sql"]
                    if isinstance(chunk.get("result"), list):
                        result_rows = len(chunk["result"])
            # Surface the generated decision insight (previously generated
            # but never emitted - the user saw a bare table).
            insight_text = str(_state_fold.get("decision_insights") or "").strip()
            logger.info(f"insight emit check: keys={sorted(_state_fold.keys())[:14]} "
                        f"has_insight={bool(insight_text)} len={len(insight_text)}")
            if insight_text:
                insight_line = 'data: ' + json.dumps(
                    {"decision_insights": insight_text},
                    ensure_ascii=False, default=str) + chr(10) + chr(10)
                yield insight_line
                sse_lines.append(insight_line)
            # OPT-M9: write result-level cache for stable queries.
            try:
                from app.services.result_cache import set_cached_result
                if final_sql and result_rows > 0:
                    # Reconstruct a small result snapshot for caching.
                    last_result = None
                    for line in sse_lines[-5:]:
                        try:
                            chunk_data = json.loads(line.replace("data: ", "").strip())
                            if isinstance(chunk_data, dict) and isinstance(chunk_data.get("result"), list):
                                last_result = chunk_data["result"][:100]
                                break
                        except Exception:
                            pass
                    if last_result is not None:
                        await set_cached_result(query, final_sql,
                                                {"rows": last_result, "row_count": result_rows},
                                                username)
            except Exception as e:
                logger.debug(f"OPT-M9 result cache set skipped: {e}")
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
            # M1: state/checkpoint (slim snapshot) + turn/ended, then flush
            # so the timeline is complete before the client polls.
            turn_reason = status
            if _state_fold.get("pending_clarify_id"):
                turn_reason = "clarify_wait"
            elif _state_fold.get("pending_approval_ticket_id"):
                turn_reason = "approval_wait"
            _CHECKPOINT_KEEP = {
                "query", "intent", "sql", "error", "history",
                "semantic_plan", "needs_approval", "approval_reason",
                "pending_approval_ticket_id", "pending_clarify_id",
                "complexity", "anomaly_status", "decision_policy",
                "decision_insights", "_username", "_role",
                # grounding context: clarify-resume used to restart
                # generate_sql with 0 tables (DDL-less LLM generation).
                "table_infos", "keywords", "metric_infos",
                "date_info", "db_info", "enriched_query",
                "glossary_matches", "matched_dimension_values",
                "retrieved_columns", "retrieved_metrics",
            }
            checkpoint = {k: v for k, v in _state_fold.items()
                          if k in _CHECKPOINT_KEEP}
            emit("state/checkpoint", checkpoint)
            emit("turn/ended", {"reason": turn_reason, "latency_ms": latency,
                                "result_rows": result_rows})
            try:
                from app.core.session_event import session_event_logger
                await session_event_logger.flush(request_id)
            except Exception as _e:
                logger.debug(f"session event flush failed: {_e}")
            await send_audit_log(request_id, username, query, sql=final_sql,
                                 status=status, latency_ms=latency, result_rows=result_rows)


def _find_toplevel_limit_idx(sql: str) -> int:
    """Index of a depth-0 LIMIT keyword (outside strings), or -1."""
    depth = 0
    i, n = 0, len(sql)
    in_str = None
    while i < n:
        c = sql[i]
        if in_str:
            if c == in_str:
                in_str = None
        elif c in ("'", '"', '`'):
            in_str = c
        elif c == '(':
            depth += 1
        elif c == ')':
            depth = max(0, depth - 1)
        elif depth == 0 and sql[i:i + 5].upper() == 'LIMIT':
            after = sql[i + 5: i + 6]
            if after in ('', ' ', chr(10), chr(9), ';'):
                return i
        i += 1
    return -1
