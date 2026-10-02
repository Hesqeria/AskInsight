"""SQL learnings store (dash-style second memory layer).

The exemplar store keeps SUCCESS pairs (question -> gold SQL). This store
keeps the opposite: pitfalls the pipeline already paid for - a validation
/execution error and the fix that made it pass. Recent learnings are
injected into the generation prompt as "known pitfalls to avoid", so the
same mistake is not made (and repaired) twice.
"""
import hashlib
from datetime import datetime

from sqlalchemy import text

from app.core.log import logger

_DDL = """
CREATE TABLE IF NOT EXISTS data_agent.nl2sql_learning (
  learning_id VARCHAR(64) NOT NULL,
  question    VARCHAR(512),
  error_msg   VARCHAR(1024),
  wrong_sql   VARCHAR(4096),
  fixed_sql   VARCHAR(4096),
  created_at  DATETIME
) ENGINE=OLAP
UNIQUE KEY(learning_id)
DISTRIBUTED BY HASH(learning_id) BUCKETS 1
PROPERTIES ('replication_num' = '1')
"""

_init_done = False


async def _ensure_table(session):
    global _init_done
    if _init_done:
        return
    try:
        await session.execute(text(_DDL))
        await session.commit()
        _init_done = True
    except Exception as e:
        logger.debug(f"learning table init skipped: {e}")


async def save_learning(session, question: str, error_msg: str,
                        wrong_sql: str, fixed_sql: str) -> bool:
    """Persist one pitfall->fix pair. Idempotent on content hash."""
    q = (question or "").strip()
    err = (error_msg or "").strip()
    w = (wrong_sql or "").strip()
    f = (fixed_sql or "").strip()
    if not q or not err or not f or w == f:
        return False
    if len(w) > 4000 or len(f) > 4000:
        return False
    lid = hashlib.md5(f"{q}|{err[:120]}|{f[:400]}".encode()).hexdigest()
    try:
        await _ensure_table(session)
        await session.execute(text(
            "INSERT INTO data_agent.nl2sql_learning "
            "(learning_id, question, error_msg, wrong_sql, fixed_sql, created_at) "
            "VALUES (:lid, :q, :e, :w, :f, :ts)"),
            {"lid": lid, "q": q[:500], "e": err[:1000],
             "w": w, "f": f, "ts": datetime.now()})
        await session.commit()
        logger.info(f"sql learning saved ({err[:60]})")
        return True
    except Exception as e:
        logger.debug(f"save_learning failed: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        return False


async def recent_learnings(session, limit: int = 3) -> list:
    """Most recent pitfalls for prompt injection."""
    try:
        rows = (await session.execute(text(
            "SELECT error_msg, wrong_sql, fixed_sql "
            "FROM data_agent.nl2sql_learning "
            "ORDER BY created_at DESC LIMIT :n"),
            {"n": limit})).fetchall()
        return [{"error": r[0], "wrong": r[1], "fixed": r[2]}
                for r in rows]
    except Exception as e:
        logger.debug(f"recent_learnings failed: {e}")
        return []


def format_for_prompt(learnings: list) -> str:
    """Render pitfalls as concise avoid-lines for the SQL prompt."""
    if not learnings:
        return "(none)"
    lines = []
    for i, l in enumerate(learnings, 1):
        err = str(l.get("error", ""))[:110].replace("\n", " ")
        fix = str(l.get("fixed", ""))[:130].replace("\n", " ")
        lines.append(f"{i}. 曾错: {err}\n   正确写法参考: {fix}")
    return "\n".join(lines)
