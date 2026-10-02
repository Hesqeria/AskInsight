"""Canonical resume engine (single source of truth).

Runs agent nodes outside the LangGraph graph for SUSPENDED-then-RESUMED
turns (M3 inbox / clarify / approval), mirroring graph.py's conditional
edges exactly so a resumed turn follows the same routing as a fresh one.

Previously three near-identical copies existed (inbox_service.run_
resume_stream, clarify_router._resume_stream, approval_router stream) -
two of which did NOT mirror the conditional edges (skipping
assess_complexity / correct_sql / wait_approval) and one grabbed the
wrong symbol for validate_sql_safety (BUG-07 pattern). All resume legs
now delegate here.
"""
from typing import Optional

from app.core.log import logger


class ShimRuntime:
    """Minimal Runtime shim exposing context + stream_writer so agent
    nodes can be called outside the graph framework."""

    def __init__(self, ctx):
        self.context = ctx

    @property
    def stream_writer(self):
        return lambda payload: None


def build_resume_context(session) -> "DataAgentContext":  # noqa: F821
    """Build a DataAgentContext for the resume stream (all repos wired)."""
    from app.agent.context import DataAgentContext
    from app.clients.milvus_client_manager import milvus_client_manager
    from app.clients.embedding_client_manager import embedding_client_manager
    from app.clients.rerank_client_manager import rerank_client_manager
    from app.repositories.doris.meta.meta_doris_repository import MetaDorisRepository
    from app.repositories.doris.dw.dw_doris_repository import DwDorisRepository
    from app.repositories.doris.value.value_doris_repository import ValueDorisRepository
    from app.repositories.milvus.column_milvus_repository import ColumnMilvusRepository
    from app.repositories.milvus.metric_milvus_repository import MetricMilvusRepository
    ctx = DataAgentContext(
        embedding_client=embedding_client_manager.client,
        rerank_client=rerank_client_manager.client,
        column_milvus_repository=ColumnMilvusRepository(milvus_client_manager.client),
        metric_milvus_repository=MetricMilvusRepository(milvus_client_manager.client),
        value_doris_repository=ValueDorisRepository(session),
        meta_doris_repository=MetaDorisRepository(session),
        dw_doris_repository=DwDorisRepository(session),
    )
    try:
        from app.repositories.doris.rl.rl_doris_repository import RlDorisRepository
        ctx["rl_doris_repository"] = RlDorisRepository(session)
    except Exception:
        pass
    return ctx


RESUME_TAIL = [
    "extract_lineage", "anomaly_detection", "drill_down_analysis",
    "decision_insight", "code_executor",
]

STAGE_LABELS = {
    "generate_sql": "Generate SQL",
    "validate_sql_safety": "Validate SQL Safety",
    "validate_sql": "Validate SQL",
    "assess_complexity": "Assess Complexity",
    "correct_sql": "Correct SQL",
    "wait_approval": "Pending Approval",
    "execute_sql": "Execute SQL",
    "extract_lineage": "Lineage Extraction",
    "anomaly_detection": "Anomaly Detection",
    "drill_down_analysis": "Drill-down Analysis",
    "decision_insight": "Decision Insight",
    "code_executor": "Code Executor",
}


def next_node(current: str, state: dict) -> Optional[str]:
    """Replicate graph.py conditional edges for the post-generate path."""
    if current == "generate_sql":
        return "validate_sql_safety"
    if current == "validate_sql_safety":
        return "validate_sql"
    if current == "validate_sql":
        return "correct_sql" if state.get("error") else "assess_complexity"
    if current == "assess_complexity":
        return ("execute_sql"
                if state.get("sql_source") == "plan_render"
                or state.get("complexity") in ("simple", "medium", "fallback")
                else "correct_sql")
    if current == "correct_sql":
        return "wait_approval" if state.get("needs_approval") else "execute_sql"
    if current == "wait_approval":
        return None if state.get("pending_approval_ticket_id") else "execute_sql"
    if current == "execute_sql":
        return RESUME_TAIL[0]
    if current in RESUME_TAIL:
        i = RESUME_TAIL.index(current)
        return RESUME_TAIL[i + 1] if i + 1 < len(RESUME_TAIL) else None
    return None


def load_node(name: str):
    """Import app.agent.nodes.<name> preferring the (state, runtime)
    LangGraph wrapper `<name>_node` when it exists. Module
    validate_sql_safety exposes BOTH the 1-arg utility and the node
    wrapper - getattr(mod, name) grabbed the utility and crashed the
    resume leg (BUG-07 pattern, fixed once here for all callers)."""
    import importlib
    mod = importlib.import_module(f"app.agent.nodes.{name}")
    fn = getattr(mod, name + "_node", None)
    return fn if callable(fn) else getattr(mod, name)




async def run_node_stream(state: dict, start_node: str, username: str,
                          session_id: str = "",
                          extra_complete: Optional[dict] = None):
    """Async generator of SSE lines: run nodes from `start_node` using
    the same routing as graph.py. Handles second-order suspension
    (PII gate -> wait_approval -> new inbox item) and emits the final
    `resume_complete` frame (merged with `extra_complete`, e.g.
    clarify_id / ticket_id) plus M1 timeline turn-end events."""
    import json as _json
    from app.clients.doris_client_manager import doris_client_manager

    request_id_ctx = None
    if session_id:
        try:
            from app.core.context import request_id_ctx_var
            request_id_ctx_var.set(session_id)
            request_id_ctx = request_id_ctx_var
        except Exception:
            pass

    current = start_node
    try:
        async with doris_client_manager.session_factory() as db_session:
            ctx = build_resume_context(db_session)
            runtime = ShimRuntime(ctx)
            while current:
                node_fn = load_node(current)
                yield "data: " + _json.dumps(
                    {"stage": STAGE_LABELS.get(current, current)},
                    ensure_ascii=False) + "\n\n"
                try:
                    update = await node_fn(state, runtime)
                except Exception as e:
                    logger.error(f"resume node {current} failed: {e}",
                                 exc_info=True)
                    yield "data: " + _json.dumps(
                        {"error": f"{current}: {e}", "stage": current},
                        ensure_ascii=False, default=str) + "\n\n"
                    return
                if isinstance(update, dict) and update:
                    state.update(update)
                    yield "data: " + _json.dumps(
                        update, ensure_ascii=False, default=str) + "\n\n"
                if current == "wait_approval" and state.get(
                        "pending_approval_ticket_id"):
                    yield "data: " + _json.dumps(
                        {"resume_suspended": True}, ensure_ascii=False,
                    ) + "\n\n"
                    return
                current = next_node(current, state)
            complete = {"resume_complete": True}
            if extra_complete:
                complete.update(extra_complete)
            yield "data: " + _json.dumps(
                complete, ensure_ascii=False) + "\n\n"
            try:
                from app.agent.events import emit
                from app.core.session_event import session_event_logger
                emit("state/checkpoint", {k: v for k, v in state.items()
                     if not str(k).startswith("_")})
                emit("turn/ended", {"reason": "resumed"})
                sid = (request_id_ctx.get() if request_id_ctx else None)
                if sid:
                    await session_event_logger.flush(sid)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"resume stream failed: {e}", exc_info=True)
        yield "data: " + _json.dumps(
            {"error": str(e)}, ensure_ascii=False, default=str) + "\n\n"
