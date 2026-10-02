"""Modeling router (PRD P4): draft -> approval ticket -> execute."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.auth import require_admin
from app.core.log import logger
from app.services import modeling_service
from app.services import gov_task_service
from app.repositories.doris.approval.approval_repository import ApprovalRepository

modeling_router = APIRouter(prefix="/api/admin/modeling")


class DraftBody(BaseModel):
    spec: dict


class ApplyBody(BaseModel):
    spec: dict


@modeling_router.post("/draft")
async def draft(body: DraftBody, user: dict = Depends(require_admin)):
    """Generate PRD 4.4-compliant DDL + 4.2 review violations."""
    try:
        ddl = modeling_service.draft_ddl(body.spec)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    return {"status": "ok", "ddl": ddl, "violations": modeling_service.review_ddl(ddl)}


@modeling_router.post("/apply")
async def apply(body: ApplyBody, user: dict = Depends(require_admin)):
    """Create an approval ticket carrying the drafted DDL."""
    try:
        ddl = modeling_service.draft_ddl(body.spec)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    violations = modeling_service.review_ddl(ddl)
    if violations:
        return JSONResponse(status_code=422, content={
            "status": "error", "message": "DDL violates modeling standards", "violations": violations})
    doris_client_manager_init()
    async with _session_factory() as session:
        repo = ApprovalRepository(session)
        ticket = await repo.create(
            request_id=body.spec.get("request_id") or "modeling",
            sql_text=ddl,
            username=user.get("sub", "unknown"),
            pii_reason="modeling_ddl",
            pii_violations=[],
        )
    if ticket is None:
        return JSONResponse(status_code=500, content={"status": "error", "message": "ticket create failed"})
    return {"status": "ok", "ticket_id": ticket.ticket_id, "ddl": ddl,
            "next": f"POST /api/approvals/{ticket.ticket_id}/decide then /execute"}


@modeling_router.post("/execute/{ticket_id}")
async def execute(ticket_id: str, user: dict = Depends(require_admin)):
    """Execute an APPROVED modeling DDL ticket (state machine: pending->approved->executed)."""
    doris_client_manager_init()
    async with _session_factory() as session:
        repo = ApprovalRepository(session)
        ticket = await repo.get(ticket_id)
        if ticket is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "ticket not found"})
        if ticket.pii_reason != "modeling_ddl":
            return JSONResponse(status_code=400, content={"status": "error", "message": "not a modeling ticket"})
        if ticket.status != "approved":
            return JSONResponse(status_code=409, content={
                "status": "error", "message": f"ticket is {ticket.status!r}, must be approved first"})
        result = await modeling_service.execute_ddl(ticket.sql_text)
        if not result.get("ok"):
            return JSONResponse(status_code=400, content={"status": "error", "message": result.get("error")})
        logger.info(f"modeling DDL executed for ticket {ticket_id} by {user.get('sub')}")

        # P4 closed loop: schema changed -> smoke-eval the pipeline in the
        # background; result lands on the post_ddl_eval gov_task row and is
        # visible on the GovDashboard tasks panel.
        import asyncio as _asyncio

        async def _post_ddl_eval():
            try:
                doris_client_manager_init()
                async with _session_factory() as es:
                    outcome = await gov_task_service.run_task_now(es, "post_ddl_eval")
                    logger.info(f"post-DDL eval for {ticket_id}: "
                                f"{outcome.get('status')} {outcome.get('detail')}")
            except Exception as eval_err:
                logger.warning(f"post-DDL eval failed: {eval_err}")

        _asyncio.get_running_loop().create_task(_post_ddl_eval())
        return {"status": "ok", "ticket_id": ticket_id, "executed": True,
                "post_ddl_eval": "started in background (see GovDashboard tasks)"}


def doris_client_manager_init():
    from app.clients.doris_client_manager import doris_client_manager
    doris_client_manager.init()


def _session_factory():
    from app.clients.doris_client_manager import doris_client_manager
    return doris_client_manager.session_factory()
