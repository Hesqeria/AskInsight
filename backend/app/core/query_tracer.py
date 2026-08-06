"""Query lifecycle tracer (Vanna #176 lesson: missing log observability).

Records per query:
  - elapsed time per stage
  - recall hit count
  - SQL generation details
  - execution result
  - error information

Outputs JSON-formatted logs for downstream analysis and monitoring.
"""
import time
import json

from app.core.log import logger


class QueryTrace:
    """Trace record for a single query."""

    def __init__(self, query: str, request_id: str):
        self.query = query
        self.request_id = request_id
        self.start_time = time.time()
        self.stages: list[dict] = []
        self._stage_start: float | None = None
        self._current_stage: str | None = None

    def start_stage(self, name: str):
        """Start recording a stage."""
        self._stage_start = time.time()
        self._current_stage = name

    def end_stage(self, name: str, detail: dict | None = None):
        """End a stage."""
        if self._stage_start is None:
            return
        elapsed = round((time.time() - self._stage_start) * 1000, 1)
        stage = {
            "stage": name,
            "elapsed_ms": elapsed,
            "detail": detail or {},
        }
        self.stages.append(stage)
        logger.info(f"[TRACE] {name}: {elapsed}ms | {json.dumps(detail, ensure_ascii=False)[:100] if detail else ''}")
        self._stage_start = None
        self._current_stage = None

    def finish(self, result_summary: str, success: bool = True):
        """Query finished, output the full trace."""
        total_elapsed = round((time.time() - self.start_time) * 1000, 1)
        slowest = max(self.stages, key=lambda s: s["elapsed_ms"]) if self.stages else None

        summary = {
            "request_id": self.request_id,
            "query": self.query[:100],
            "total_ms": total_elapsed,
            "success": success,
            "stages_count": len(self.stages),
            "slowest_stage": slowest["stage"] if slowest else None,
            "slowest_ms": slowest["elapsed_ms"] if slowest else 0,
            "result": result_summary[:200],
        }

        if total_elapsed > 30000:
            logger.warning(f"[TRACE-SLOW] {json.dumps(summary, ensure_ascii=False)}")
        elif not success:
            logger.error(f"[TRACE-FAIL] {json.dumps(summary, ensure_ascii=False)}")
        else:
            logger.info(f"[TRACE-DONE] {json.dumps(summary, ensure_ascii=False)}")

        return summary


# Global trace instance (a new one is created per request)
_current_trace: QueryTrace | None = None


def start_trace(query: str, request_id: str) -> QueryTrace:
    """Start tracing a query."""
    global _current_trace
    _current_trace = QueryTrace(query, request_id)
    return _current_trace


def get_trace() -> QueryTrace | None:
    """Get the current trace instance."""
    return _current_trace


def trace_stage(name: str):
    """Decorator: automatically record node timing."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            trace = get_trace()
            if trace:
                trace.start_stage(name)
            try:
                result = await func(*args, **kwargs)
                if trace:
                    detail = {}
                    if isinstance(result, dict):
                        if "sql" in result:
                            detail["sql_len"] = len(result.get("sql", ""))
                        if "result" in result and isinstance(result["result"], list):
                            detail["result_rows"] = len(result["result"])
                        if "error" in result and result["error"]:
                            detail["error"] = result["error"][:80]
                    trace.end_stage(name, detail)
                return result
            except Exception as e:
                if trace:
                    trace.end_stage(name, {"error": str(e)[:80]})
                raise
        return wrapper
    return decorator
