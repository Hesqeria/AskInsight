"""User correction API: submit a correction when SQL results are unsatisfactory."""
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.core.auth import verify_token
from app.core.log import logger

feedback_router = APIRouter()


class FeedbackSchema(BaseModel):
    query: str
    wrong_sql: str
    corrected_sql: str


@feedback_router.post("/api/feedback")
async def submit_feedback(
    body: FeedbackSchema,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """User submits a corrected SQL (improves accuracy over time)."""
    # Safety check via validate_sql_safety (NFKC + homoglyph aware)
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    is_safe, reason = validate_sql_safety(body.corrected_sql)
    if not is_safe:
        return {'status': 'error', 'message': f'Corrected SQL rejected: {reason}'}
    if not body.corrected_sql.strip().upper().startswith('SELECT'):
        return {'status': 'error', 'message': 'Corrected SQL must be a SELECT statement'}

    try:
        sql = """INSERT INTO feedback_log (id, query, wrong_sql, corrected_sql, username, created_at)
                 VALUES (:id, :query, :wrong_sql, :corrected_sql, :username, :created_at)"""
        await session.execute(text(sql), {
            "id": str(uuid.uuid4()),
            "query": body.query[:500],
            "wrong_sql": body.wrong_sql[:2000],
            "corrected_sql": body.corrected_sql[:2000],
            "username": user.get("sub", "anonymous"),
            "created_at": datetime.now(),
        })
        await session.commit()
        logger.info(f"User {user.get('sub')} submitted correction: {body.query[:30]}")
        # Exemplar flywheel: a human-verified correction becomes a
        # few-shot pair immediately (best-effort).
        try:
            from app.services import exemplar_store
            await exemplar_store.add_exemplar(
                session, body.query, body.corrected_sql,
                exemplar_store._SOURCE_CORRECTION, user.get("sub", ""))
        except Exception as ex_err:
            logger.warning(f"exemplar add skipped: {ex_err}")
        return {"status": "ok", "message": "Correction recorded; similar queries will reference this example next time"}
    except Exception as e:
        logger.error(f"Failed to submit correction: {e}")
        return {"status": "error", "message": str(e)}


@feedback_router.get("/api/glossary")
async def list_glossary(
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """View the glossary."""
    from app.agent.nodes.glossary_matching import GLOSSARY_COLS
    result = await session.execute(
        text(f"SELECT {GLOSSARY_COLS} FROM data_agent.glossary LIMIT 100"))
    return {"terms": [dict(row._mapping) for row in result.fetchall()]}


class QualitySchema(BaseModel):
    request_id: str
    rating: int
    comment: str = ""


class FlowbackSchema(BaseModel):
    query: str
    sql: str
    entity: str = ""
    complexity: str = ""
    rating: int = 1  # 1=满意(回流 few_shot), 0=不满意(记录 feedback_log)


@feedback_router.post("/api/quality/rate")
async def rate_answer(body: QualitySchema, user: dict = Depends(verify_token), session: AsyncSession = Depends(get_meta_session)):
    from sqlalchemy import text as _text
    import uuid as _uuid
    try:
        # 1) Persist the rating as before (legacy audit trail).
        await session.execute(_text("INSERT INTO feedback_log (id, request_id, query, wrong_sql, corrected_sql, username, created_at) VALUES (:id, :rid, :q, '', '', :u, NOW())"), {"id": str(_uuid.uuid4()), "rid": body.request_id, "q": "[QUALITY_RATING=" + str(body.rating) + "] " + body.comment, "u": user.get("sub", "anonymous")})
        await session.commit()
    except Exception as e:
        logger.error(f"Failed to record rating: {e}")

    # 2) RL reward attribution: if this request_id corresponds to a
    #    logged decision_insight policy, update that policy's Beta
    #    posterior. Failure here does NOT fail the rating endpoint -
    #    the user's rating is still recorded above.
    try:
        from app.repositories.doris.rl.rl_doris_repository import RlDorisRepository
        from app.rl import ThompsonSampler
        from app.core.metrics import RL_REWARD
        from app.core.log import logger as _log

        rl_repo = RlDorisRepository(session)
        decision = await rl_repo.get_decision_by_request(body.request_id)
        if decision is None:
            return {"status": "ok", "rating": body.rating,
                    "rl_updated": False,
                    "note": "no decision logged for this request_id"}
        policy_name = decision["policy_name"]

        # Load current posterior, apply Bayesian update, persist.
        stats_map = await rl_repo.get_all_stats()
        if policy_name not in stats_map:
            _log.warning(f"RL reward for unknown policy {policy_name!r}; skipping")
            return {"status": "ok", "rating": body.rating, "rl_updated": False}

        reward = 1.0 if int(body.rating) >= 1 else 0.0
        new_stats = ThompsonSampler.update_stats(stats_map[policy_name], reward)
        await rl_repo.upsert_stats(new_stats)

        # Metric for observability.
        try:
            RL_REWARD.labels(policy=policy_name,
                             reward="positive" if reward >= 0.5 else "negative").inc()
        except Exception:
            pass

        _log.info(
            f"RL reward applied: policy={policy_name} reward={reward} "
            f"new_alpha={new_stats.alpha:.2f} new_beta={new_stats.beta:.2f} "
            f"expected={new_stats.expected_reward:.3f}"
        )
        return {"status": "ok", "rating": body.rating, "rl_updated": True,
                "policy": policy_name,
                "expected_reward": round(new_stats.expected_reward, 4)}
    except Exception as e:
        logger.warning(f"RL reward attribution failed (rating still recorded): {e}")
        return {"status": "ok", "rating": body.rating, "rl_updated": False}


@feedback_router.post("/api/feedback/flowback")
async def flowback(body: FlowbackSchema, user: dict = Depends(verify_token), session: AsyncSession = Depends(get_meta_session)):
    """User 👍/👎 drives few_shot_examples auto-accumulation.

    - rating=1: query+SQL pair flows into few_shot_examples (deduped, safe SELECT only).
    - rating=0: recorded into feedback_log for human review.
    """
    from app.services.feedback_flowback import flowback_satisfied, record_dissatisfied

    if body.rating >= 1:
        result = await flowback_satisfied(
            session, query=body.query, sql=body.sql,
            entity=body.entity, complexity=body.complexity,
        )
        return {"status": "ok", "action": "flowback", "result": result}

    result = await record_dissatisfied(
        session, query=body.query, sql=body.sql,
        comment=body.comment, username=user.get("sub", "anonymous"),
    )
    return {"status": "ok", "action": "recorded", "result": result}


@feedback_router.get("/api/feedback/few-shot")
async def list_few_shot(user: dict = Depends(verify_token), session: AsyncSession = Depends(get_meta_session)):
    """List few_shot_examples (for monitoring / monthly review)."""
    result = await session.execute(
        text("""SELECT example_id, question, sql_text, entity, complexity, source,
                       satisfaction, business_domain, hit_count, last_used_at, created_at
                FROM data_agent.few_shot_examples ORDER BY created_at DESC LIMIT 200""")
    )
    return {"examples": [dict(r._mapping) for r in result.fetchall()]}


@feedback_router.get("/api/quality/stats")
async def quality_stats(user: dict = Depends(verify_token), session: AsyncSession = Depends(get_meta_session)):
    from sqlalchemy import text as _text
    result = await session.execute(_text("SELECT COUNT(*) as total, SUM(CASE WHEN query LIKE '[QUALITY_RATING=1]%' THEN 1 ELSE 0 END) as good, SUM(CASE WHEN query LIKE '[QUALITY_RATING=0]%' THEN 1 ELSE 0 END) as bad FROM feedback_log WHERE query LIKE '[QUALITY_RATING=%'"))
    row = result.fetchone()
    return {"total": row[0] if row else 0, "good": row[1] if row else 0, "bad": row[2] if row else 0}
