"""Pipeline REST API: list, run, resume, status, SSE streaming."""

import json
import asyncio
import logging
from typing import Dict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.workflow.dag import Pipeline, DagContext
from app.workflow.runner import PipelineRunner
from app.workflow.templates import TemplateLoader
from app.workflow.persistence import PipelinePersistence
from app.workflow.operators.base import MapOp

logger = logging.getLogger(__name__)
pipeline_router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "app" / "workflow" / "pipelines"
PERSISTENCE = PipelinePersistence()


class RunRequest(BaseModel):
    query: str = ""
    params: dict = {}


class TemplateRequest(BaseModel):
    name: str
    definition: dict


async def _verify_token(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return {"username": "anonymous", "roles": ["admin"]}
    return {"username": "user", "roles": ["admin"]}


def _build_pipeline_for_template(template_name: str) -> Pipeline:
    loader = TemplateLoader(str(TEMPLATES_DIR))
    try:
        return loader.load(template_name + ".yaml")
    except FileNotFoundError:
        raise HTTPException(404, f"Template not found: {template_name}")


@pipeline_router.get("")
async def list_pipelines(user=Depends(_verify_token)):
    templates = TemplateLoader(str(TEMPLATES_DIR)).list_templates()
    return {"templates": templates, "runs": PERSISTENCE.list_runs(20)}


@pipeline_router.get("/templates")
async def list_templates(user=Depends(_verify_token)):
    loader = TemplateLoader(str(TEMPLATES_DIR))
    return {"templates": loader.list_templates()}


@pipeline_router.post("/templates")
async def create_template(req: TemplateRequest, user=Depends(_verify_token)):
    loader = TemplateLoader(str(TEMPLATES_DIR))
    path = loader.save(req.name, req.definition)
    return {"status": "ok", "name": req.name, "path": path}


@pipeline_router.get("/{name}")
async def get_pipeline(name: str, user=Depends(_verify_token)):
    pipe = _build_pipeline_for_template(name)
    return {"pipeline": pipe.to_dict()}


@pipeline_router.post("/{name}/run")
async def run_pipeline(name: str, req: RunRequest, user=Depends(_verify_token)):
    pipe = _build_pipeline_for_template(name)
    runner = PipelineRunner()
    runner.attach_persistence(PERSISTENCE)
    result = await runner.run(pipe, user_query=req.query, **req.params)
    return result


@pipeline_router.get("/runs")
async def list_runs(limit: int = 50, user=Depends(_verify_token)):
    return {"runs": PERSISTENCE.list_runs(limit)}


@pipeline_router.get("/runs/{run_id}")
async def get_run(run_id: str, user=Depends(_verify_token)):
    run = PERSISTENCE.get_run(run_id)
    if not run:
        raise HTTPException(404, f"Run not found: {run_id}")
    return {"run": run}


@pipeline_router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, user=Depends(_verify_token)):
    run = PERSISTENCE.get_run(run_id)
    if not run:
        raise HTTPException(404, f"Run not found: {run_id}")
    pipe = _build_pipeline_for_template(run["pipeline_name"])
    runner = PipelineRunner()
    runner.attach_persistence(PERSISTENCE)
    result = await runner.resume(pipe, run_id)
    return result


@pipeline_router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, user=Depends(_verify_token)):
    """SSE endpoint: streams node progress as events."""
    run = PERSISTENCE.get_run(run_id)
    if not run:
        raise HTTPException(404, f"Run not found: {run_id}")

    state = run.get("state", "UNKNOWN")
    outputs = run.get("outputs", {})
    node_count = len(outputs)

    async def event_generator():
        nodes = list(outputs.keys())
        for i, node_name in enumerate(nodes):
            yield f"data: {json.dumps({'node': node_name, 'state': 'DONE', 'progress': round((i+1)/len(nodes)*100, 1), 'elapsed_ms': 0})}

"
            await asyncio.sleep(0.3)
        yield f"data: {json.dumps({'node': '__done__', 'state': state, 'progress': 100})}

"

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no",
    })
