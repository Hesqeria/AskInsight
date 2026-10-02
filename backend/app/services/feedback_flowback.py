"""Feedback flow-back: user 👍/👎 drives few_shot_examples auto-accumulation.

OPT-M2 (数仓问数实际应用-PRD):
  - 👍 (satisfied, rating=1): the question+SQL pair flows into few_shot_examples
    automatically (deduped), so similar queries reference it next time.
  - 👎 (dissatisfied, rating=0): recorded via feedback_log for human review.

Business rules:
  - Only SELECT statements are accepted (validated by validate_sql_safety).
  - Dangerous SQL (drop/delete/update/... ) is never stored.
  - Dedup by normalized question text.
  - Hit tracking: hit_count/last_used_at bumped on recall for the monthly
    pruning job (eliminate examples with hit_rate < threshold).
"""
import re
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.nodes.validate_sql_safety import validate_sql_safety
from app.core.log import logger

_DANGEROUS_RE = re.compile(
    r"\b(drop|delete|truncate|alter|insert|update|grant|revoke|execute)\b",
    re.IGNORECASE,
)

# entity -> business_domain mapping (must stay in sync with few_shot loader)
_ENTITY_DOMAIN = {
    "Order": "order", "User": "user", "SKU": "sku", "Category": "sku",
    "Campaign": "activity", "Region": "region", "Logistics": "logistics",
    "Payment": "payment", "Review": "review", "综合": "comprehensive",
}


def _normalize_question(q: str) -> str:
    """Normalize for dedup: strip whitespace/punctuation, drop filler 的, lowercase."""
    q = q or ""
    q = re.sub(r"\s+", "", q)
    q = re.sub(r"[的]|，。！？、；：,.!?;:（）()【】\[\]]", "", q)
    return q.lower()


async def flowback_satisfied(
    session: AsyncSession,
    query: str,
    sql: str,
    entity: str = "",
    complexity: str = "",
) -> dict:
    """Flow a satisfied query+SQL pair into few_shot_examples.

    Returns {"inserted": bool, "reason": str}.
    """
    sql = (sql or "").strip()
    query = (query or "").strip()
    if not query or not sql:
        return {"inserted": False, "reason": "empty query or sql"}

    # Safety gates.
    is_safe, reason = validate_sql_safety(sql)
    if not is_safe:
        return {"inserted": False, "reason": f"unsafe sql: {reason}"}
    if not sql.upper().startswith("SELECT"):
        return {"inserted": False, "reason": "not a SELECT"}
    if _DANGEROUS_RE.search(sql):
        return {"inserted": False, "reason": "dangerous keyword"}

    # Dedup by normalized question.
    norm = _normalize_question(query)
    existing = await session.execute(
        text("SELECT question FROM data_agent.few_shot_examples"),
    )
    # Efficient dedup: fetch all questions once (small table).
    q_rows = existing.fetchall()
    for (q,) in q_rows:
        if _normalize_question(q) == norm:
            return {"inserted": False, "reason": "duplicate question"}

    domain = _ENTITY_DOMAIN.get(entity, "comprehensive")
    example_id = f"fb_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    try:
        await session.execute(
            text(
                """INSERT INTO data_agent.few_shot_examples
                   (example_id, question, sql_text, entity, complexity, source, satisfaction, business_domain, created_at)
                   VALUES (:eid, :q, :sql, :ent, :cx, 'FEEDBACK', 5, :dom, :ts)"""
            ),
            {
                "eid": example_id, "q": query[:500], "sql": sql[:4000],
                "ent": entity, "cx": complexity, "dom": domain,
                "ts": datetime.now(),
            },
        )
        await session.commit()
        logger.info(f"Few-shot flowback inserted: {example_id} domain={domain}")
        return {"inserted": True, "reason": "inserted", "example_id": example_id}
    except Exception as e:
        logger.error(f"Few-shot flowback failed: {e}")
        return {"inserted": False, "reason": str(e)}


async def record_dissatisfied(
    session: AsyncSession,
    query: str,
    sql: str,
    comment: str = "",
    username: str = "anonymous",
) -> dict:
    """Record a 👎 rating into feedback_log for human review."""
    try:
        await session.execute(
            text(
                """INSERT INTO data_agent.feedback_log (id, query, wrong_sql, corrected_sql, username, created_at)
                   VALUES (:id, :q, :sql, '', :u, :ts)"""
            ),
            {
                "id": str(uuid.uuid4()), "q": (query or "")[:500],
                "sql": (sql or "")[:2000], "u": username,
                "ts": datetime.now(),
            },
        )
        await session.commit()
        logger.info(f"Dissatisfied feedback recorded: {query[:30]}")
        return {"recorded": True}
    except Exception as e:
        logger.error(f"Failed to record dissatisfied: {e}")
        return {"recorded": False, "reason": str(e)}


async def bump_hit(example_id: str, session: AsyncSession) -> None:
    """Bump hit_count/last_used_at when an example is recalled."""
    try:
        await session.execute(
            text(
                """UPDATE data_agent.few_shot_examples
                   SET hit_count = hit_count + 1, last_used_at = :ts
                   WHERE example_id = :eid"""
            ),
            {"eid": example_id, "ts": datetime.now()},
        )
        await session.commit()
    except Exception as e:
        logger.debug(f"bump_hit failed (non-fatal): {e}")
