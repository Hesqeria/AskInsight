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
    # P3-D2: safety check on corrected_sql (only SELECT allowed)
    import re as _re
    dangerous = _re.search(r'(drop|delete|truncate|alter|insert|update|grant|revoke)', body.corrected_sql, _re.IGNORECASE)
    if dangerous:
        return {'status': 'error', 'message': f'Corrected SQL contains dangerous operation: {dangerous.group()}'}
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
    result = await session.execute(text("SELECT term, standard_name, table_name, column_name, description FROM glossary LIMIT 100"))
    return {"terms": [dict(row) for row in result.fetchall()]}


class QualitySchema(BaseModel):
    request_id: str
    rating: int
    comment: str = ""


@feedback_router.post("/api/quality/rate")
async def rate_answer(body: QualitySchema, user: dict = Depends(verify_token), session: AsyncSession = Depends(get_meta_session)):
    from sqlalchemy import text as _text
    import uuid as _uuid
    try:
        await session.execute(_text("INSERT INTO feedback_log (id, request_id, query, wrong_sql, corrected_sql, username, created_at) VALUES (:id, :rid, :q, '', '', :u, NOW())"), {"id": str(_uuid.uuid4()), "rid": body.request_id, "q": "[QUALITY_RATING=" + str(body.rating) + "] " + body.comment, "u": user.get("sub", "anonymous")})
        await session.commit()
        return {"status": "ok", "rating": body.rating}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@feedback_router.get("/api/quality/stats")
async def quality_stats(user: dict = Depends(verify_token), session: AsyncSession = Depends(get_meta_session)):
    from sqlalchemy import text as _text
    result = await session.execute(_text("SELECT COUNT(*) as total, SUM(CASE WHEN query LIKE '[QUALITY_RATING=1]%' THEN 1 ELSE 0 END) as good, SUM(CASE WHEN query LIKE '[QUALITY_RATING=0]%' THEN 1 ELSE 0 END) as bad FROM feedback_log WHERE query LIKE '[QUALITY_RATING=%'"))
    row = result.fetchone()
    return {"total": row[0] if row else 0, "good": row[1] if row else 0, "bad": row[2] if row else 0}
