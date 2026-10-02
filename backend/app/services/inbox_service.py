"""Unified async interaction inbox service (PRD M3).

Borrowed from dsh's next-step/next-turn inbox + event-sourced resume:

- suspend(): any graph interaction point writes ONE inbox row (plus its
  domain table for compatibility) and ends the turn normally.
- respond(): routes by `kind` to a registered handler; the handler
  returns a state patch + resume node; the state is rebuilt from the
  M1 `state/checkpoint` event (no manual per-node replay lists).
- register_handler(): adding a NEW interaction point (e.g. dimension
  value confirmation) means writing one handler function - no graph
  changes, no new tables (acceptance criterion #2).
- steer semantics (FR6): respond(steer=True) injects the response as
  context without resuming; the next user turn consumes it.

Handlers registry keyed by kind:
    "clarify"  -> merge user response into the semantic plan, resume
                  from generate_sql.
    "approval" -> persist the decision; approved resumes from
                  execute_sql, rejected ends.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import logger

# Canonical resume engine (single source of truth) - compat re-exports.
# Routers/tests historically imported these from inbox_service.
from app.services.resume_engine import (  # noqa: F401
    ShimRuntime, build_resume_context, load_node, next_node,
    run_node_stream, STAGE_LABELS,
)
run_resume_stream = run_node_stream
_next_node = next_node  # tests import this private name


@dataclass
class HandlerResult:
    resume_node: Optional[str]        # None => no graph resume (closed)
    state_patch: dict = field(default_factory=dict)
    outcome: str = "done"             # done|resumed|rejected|amended|steered
    sse_stream: bool = False          # caller returns SSE when True
    extra: dict = field(default_factory=dict)


HandlerFn = Callable[..., Awaitable[HandlerResult]]


class InboxService:
    def __init__(self):
        self._handlers: dict[str, HandlerFn] = {}

    def register_handler(self, kind: str, fn: HandlerFn) -> None:
        self._handlers[kind] = fn

    def kinds(self) -> list[str]:
        return sorted(self._handlers)

    async def suspend(self, repo_session: AsyncSession, session_id: str,
                      kind: str, payload: dict,
                      channel: str = "next_step") -> Optional[str]:
        """Persist one pending interaction. Returns inbox_id (None on
        DB failure - callers degrade to their legacy flow, never hang)."""
        from app.repositories.doris.inbox.inbox_repository import (
            InboxRepository,
        )
        repo = InboxRepository(repo_session)
        item = await repo.create(session_id, kind, payload, channel=channel)
        if item is None:
            return None
        try:
            from app.agent.events import emit
            emit("inbox/suspended", {
                "inbox_id": item.inbox_id, "kind": kind,
                "channel": item.channel, "session_id": session_id,
            })
        except Exception:
            pass
        logger.info(f"inbox suspend: {item.inbox_id} kind={kind} "
                    f"channel={item.channel} session={session_id}")
        return item.inbox_id

    async def respond(self, repo_session: AsyncSession, inbox_id: str,
                      response: dict, username: str = "anonymous",
                      steer: bool = False, role: str = "") -> dict:
        """Claim an inbox item, run its handler, return what to do next.

        Returns {status, handler_result?, item, error?}. The router turns
        HandlerResult into an SSE resume stream or a JSON response."""
        from app.repositories.doris.inbox.inbox_repository import (
            InboxRepository,
        )
        repo = InboxRepository(repo_session)
        item = await repo.get(inbox_id)
        if item is None:
            return {"status": "error", "error": "not_found",
                    "message": f"inbox item {inbox_id!r} not found"}
        if item.status != "pending":
            return {"status": "error", "error": "conflict",
                    "message": f"item status is {item.status!r}"}
        handler = self._handlers.get(item.kind)
        if handler is None:
            await repo.mark(inbox_id, "discarded")
            return {"status": "error", "error": "no_handler",
                    "message": f"no handler registered for kind "
                               f"{item.kind!r}"}
        await repo.mark(inbox_id, "claimed")

        # steer (FR6): inject context, keep the pending interaction open.
        if steer:
            patch = await self._steer(item, response)
            await repo.mark(inbox_id, "pending")  # still awaiting answer
            return {"status": "steered", "item": item.to_dict(),
                    "injected": patch}

        try:
            try:
                result = await handler(item, response, repo_session,
                                       username=username, role=role)
            except TypeError:
                # Custom handlers may not declare the role kwarg.
                result = await handler(item, response, repo_session,
                                       username=username)
        except Exception as e:
            logger.error(f"inbox handler {item.kind} failed: {e}",
                         exc_info=True)
            await repo.mark(inbox_id, "pending")
            return {"status": "error", "error": "handler_failed",
                    "message": str(e)}
        # Handler succeeded. Terminal outcomes close the interaction;
        # "amended" / "parse_failed" mean the user is still mid-dialog,
        # so the inbox item goes back to pending (multi-round capable).
        if result.outcome in ("amended", "parse_failed", "forbidden"):
            await repo.mark(inbox_id, "pending")
        else:
            await repo.mark(inbox_id, "done")
        try:
            from app.agent.events import emit
            emit("inbox/responded", {
                "inbox_id": inbox_id, "kind": item.kind,
                "outcome": result.outcome,
            })
        except Exception:
            pass
        return {"status": result.outcome, "handler_result": result,
                "item": item.to_dict()}

    async def _steer(self, item, response: dict) -> dict:
        """Steer = inject new info into the session context WITHOUT
        resuming/answering (dsh steer semantics, FR6). Persisted into
        the checkpoint's history so the next turn sees it."""
        injected = {"steer": response, "inbox_id": item.inbox_id,
                    "kind": item.kind}
        try:
            from app.agent.events import emit
            emit("inbox/steered", injected)
        except Exception:
            pass
        return injected


inbox_service = InboxService()


# --------------------------------------------------------------------- #
# State rebuild + linear resume engine (FR3/FR4)
# --------------------------------------------------------------------- #
async def rebuild_state(session_id: str) -> Optional[dict]:
    """Rebuild DataAgentState from the latest state/checkpoint event
    (M1). Returns None when the event log is unavailable (caller falls
    back to the domain table's own snapshot)."""
    from app.repositories.doris.session_event.session_event_repository import (
        SessionEventRepository,
    )
    from app.clients.doris_client_manager import doris_client_manager
    try:
        async with doris_client_manager.session_factory() as db:
            repo = SessionEventRepository(db)
            ckpt = await repo.latest_checkpoint(session_id)
            if ckpt is None:
                return None
            state = dict(ckpt.payload or {})
            state.setdefault("query", "")
            state["error"] = None
            return state
    except Exception as e:
        logger.warning(f"inbox rebuild_state failed: {e}")
        return None



# --------------------------------------------------------------------- #
# Built-in handlers: clarify + approval (FR3 route-by-kind)
# --------------------------------------------------------------------- #
async def _clarify_handler(item, response: dict, session, username: str) -> HandlerResult:
    """Merge the user's clarification response and resume generate_sql.

    Mirrors clarify_router.resume_clarify's core flow (free-text parse,
    plan merge, rounds cap) but reuses the inbox state rebuild."""
    from app.repositories.doris.clarify.clarify_repository import (
        ClarifyRepository,
    )
    clarify_id = item.payload.get("ref_id") or item.payload.get("clarify_id")
    repo = ClarifyRepository(session)
    sess = await repo.get(clarify_id) if clarify_id else None
    if sess is None:
        return HandlerResult(resume_node=None, outcome="error",
                             extra={"message": f"clarify session "
                                     f"{clarify_id!r} not found"})
    response_type = response.get("response_type", "selection")
    if response_type == "cancel":
        await repo.update_response(clarify_id=clarify_id,
                                   user_response={"response_type": "cancel"},
                                   final_plan=sess.initial_plan,
                                   status="abandoned")
        return HandlerResult(resume_node=None, outcome="cancelled")

    user_response = {
        "response_type": response_type,
        "selections": response.get("selections") or {},
        "free_text": response.get("free_text"),
        "confirmed": response.get("confirmed", True),
    }
    if response_type == "free_text" and user_response["free_text"]:
        from app.agent.free_text_parser import aparse_free_text
        parsed = await aparse_free_text(sess.question,
                                        user_response["free_text"])
        if parsed:
            user_response["selections"] = {
                **parsed, **user_response["selections"]}
        else:
            return HandlerResult(resume_node=None, outcome="parse_failed",
                                 extra={"re_prompt": True, "message":
                                        "未能理解您的回复,请从下方选项中选择"})
    if response_type == "candidate" and response.get("candidate_id"):
        user_response["candidate_id"] = response["candidate_id"]
        merged = _pick_candidate(sess, response["candidate_id"])
        if merged is None:
            return HandlerResult(resume_node=None, outcome="error",
                                 extra={"message": "候选方案不存在"})
    else:
        from app.agent.nodes.merge_clarification import (
            apply_clarification_to_plan,
        )
        merged = apply_clarification_to_plan(sess.initial_plan,
                                             user_response)
    confirmed = bool(user_response.get("confirmed", True))
    rounds = sess.rounds + (0 if confirmed else 1)
    status = "confirmed"
    if not confirmed and rounds <= 3:
        status = "amended"
    elif rounds > 3:
        merged.setdefault("notes", []).append("forced execute after 3 rounds")
    await repo.update_response(clarify_id=clarify_id,
                               user_response=user_response,
                               final_plan=merged, status=status,
                               rounds=rounds)
    if status == "amended":
        from app.agent.nodes.ask_clarification import (
            detect_missing_fields, build_suggestions,
        )
        missing = detect_missing_fields(merged)
        return HandlerResult(resume_node=None, outcome="amended",
                             extra={"merged_plan": merged,
                                    "missing_fields": missing,
                                    "suggestions": build_suggestions(
                                        merged, missing),
                                    "rounds": rounds})
    # Confirmed: rebuild state from checkpoint, patch the merged plan.
    state = await rebuild_state(item.session_id) or {
        "query": sess.question, "history": [], "_username": username}
    state.update({
        "query": sess.question,
        "semantic_plan": merged,
        "pending_clarify_id": None,
        "clarify_user_response": user_response,
        "error": None,
    })
    return HandlerResult(resume_node="generate_sql", outcome="confirmed",
                         sse_stream=True,
                         extra={"state": state, "clarify_id": clarify_id})


def _pick_candidate(sess, candidate_id: str) -> Optional[dict]:
    try:
        payload = sess.suggestions_given
        if isinstance(payload, dict):
            cands = payload.get("candidates") or []
        elif isinstance(payload, list):
            cands = [c for c in payload if isinstance(c, dict)
                     and c.get("_candidate_id")]
        else:
            cands = []
        for c in cands:
            if c.get("_candidate_id") == candidate_id:
                return c
    except Exception:
        pass
    return None


_APPROVER_ROLES = {"L3_engineer", "L4_admin", "admin"}


async def _approval_handler(item, response: dict, session, username: str,
                            role: str = "") -> HandlerResult:
    """Persist an approval decision; approved resumes execute_sql from
    the ticket's SQL (authoritative - it's what was reviewed).

    Deciding requires an approver role (mirrors the legacy
    /api/approvals/{id}/decide gate - fail-closed)."""
    from app.repositories.doris.approval.approval_repository import (
        ApprovalRepository,
    )
    if role not in _APPROVER_ROLES:
        return HandlerResult(
            resume_node=None, outcome="forbidden",
            extra={"message": "L3_engineer role or above required to "
                              "decide tickets"})
    ticket_id = item.payload.get("ref_id") or item.payload.get("ticket_id")
    decision = response.get("decision", "approved")
    repo = ApprovalRepository(session)
    ticket = await repo.get(ticket_id) if ticket_id else None
    if ticket is None:
        return HandlerResult(resume_node=None, outcome="error",
                             extra={"message": f"ticket {ticket_id!r} "
                                     f"not found"})
    if ticket.status != "pending":
        return HandlerResult(resume_node=None, outcome="error",
                             extra={"message": f"ticket already "
                                     f"{ticket.status!r}"})
    updated = await repo.decide(
        ticket_id=ticket_id, decision=decision,
        decided_by=username, decision_note=response.get("note", ""),
    )
    try:
        from app.services.approval_policy import emit_decided_event
        emit_decided_event(ticket_id, decision, username,
                           response.get("note", ""))
    except Exception:
        pass
    if decision != "approved":
        return HandlerResult(resume_node=None, outcome="rejected",
                             extra={"ticket": updated.to_dict()
                                    if updated else None})
    state = await rebuild_state(item.session_id) or {}
    state.update({
        "sql": ticket.sql_text,
        "needs_approval": False,
        "pending_approval_ticket_id": None,
        "error": None,
        # Sticky approval: the resumed execute_sql must not re-ask.
        "_pii_approved": True,
    })
    state.setdefault("query", ticket.pii_reason or "[approved resume]")
    state.setdefault("_username", ticket.username or username)
    return HandlerResult(resume_node="execute_sql", outcome="approved",
                         sse_stream=True, extra={"state": state})


def register_builtin_handlers() -> InboxService:
    inbox_service.register_handler("clarify", _clarify_handler)
    inbox_service.register_handler("approval", _approval_handler)
    return inbox_service


register_builtin_handlers()
