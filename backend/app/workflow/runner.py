"""PipelineRunner: topological execution with error handling."""

import time
import logging
from typing import Any, Dict
from app.workflow.dag import Pipeline, DagContext

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Executes a Pipeline topologically. Optionally attached to persistence."""

    def __init__(self):
        self._persistence = None

    async def run(self, pipeline: Pipeline, **inputs: Any) -> Dict[str, Any]:
        ctx = DagContext(pipeline_name=pipeline.name)
        ctx.share_data["_pipeline_input"] = inputs.get("user_query", inputs)
        ctx.share_data["_inputs"] = inputs

        order = pipeline.topological_order()
        started_at = time.time()

        for node in order:
            try:
                logger.info(f"[{ctx.run_id}] Running {node.name}")
                await node.execute(ctx)
            except Exception as e:
                logger.error(f"[{ctx.run_id}] FAILED {node.name}: {e}")
                ctx.share_data["_error"] = {"node": node.name, "error": str(e)}
                ctx.share_data["_state"] = "FAILED"
                if self._persistence:
                    self._persistence.save_run(ctx, "FAILED", str(e))
                return {
                    "state": "FAILED", "error": str(e), "node": node.name,
                    "run_id": ctx.run_id, "outputs": ctx.node_outputs,
                }

        elapsed = round(time.time() - started_at, 2)
        ctx.share_data["_state"] = "DONE"
        ctx.share_data["_elapsed"] = elapsed
        logger.info(f"[{ctx.run_id}] DONE ({elapsed}s)")

        if self._persistence:
            self._persistence.save_run(ctx, "DONE")

        return {
            "state": "DONE", "run_id": ctx.run_id, "elapsed": elapsed,
            "outputs": ctx.node_outputs, "node_count": len(order),
        }

    async def resume(self, pipeline: Pipeline, run_id: str) -> Dict[str, Any]:
        if not self._persistence:
            return {"state": "NO_PERSISTENCE", "run_id": run_id}

        state = self._persistence.get_run(run_id)
        if not state:
            return {"state": "NOT_FOUND", "run_id": run_id}

        ctx = DagContext(pipeline_name=pipeline.name, run_id=run_id)
        ctx.share_data = state.get("share_data", {})
        ctx.node_outputs = state.get("outputs", {})

        order = pipeline.topological_order()
        started_at = time.time()

        for node in order:
            if node.name in ctx.node_outputs:
                logger.info(f"[{run_id}] SKIP (cached) {node.name}")
                continue
            try:
                logger.info(f"[{run_id}] RESUME {node.name}")
                await node.execute(ctx)
            except Exception as e:
                logger.error(f"[{run_id}] FAILED {node.name}: {e}")
                return {"state": "FAILED", "error": str(e), "node": node.name, "run_id": run_id, "outputs": ctx.node_outputs}

        elapsed = round(time.time() - started_at, 2)
        return {"state": "DONE", "run_id": run_id, "elapsed": elapsed, "outputs": ctx.node_outputs}

    def attach_persistence(self, persistence) -> None:
        self._persistence = persistence
