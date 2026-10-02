"""Large-result spill store (PRD M10).

dsh spill insight: store the FULL result, give the model/frontend a
locator + head/tail preview, fetch the rest on demand.

Thresholds (env-overridable):
  SPILL_MIN_ROWS   default 500 rows
  SPILL_MIN_BYTES  default 256 KB serialized
  SPILL_TTL_HOURS  default 168 (7 days)

Security note: spill rows are ALREADY PII-masked by execute_sql before
they reach save_spill - the store never re-masks.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text

from app.core.log import logger

SPILL_MIN_ROWS = int(os.getenv("SPILL_MIN_ROWS", "500"))
SPILL_MIN_BYTES = int(os.getenv("SPILL_MIN_BYTES", str(256 * 1024)))
SPILL_TTL_HOURS = int(os.getenv("SPILL_TTL_HOURS", "168"))
SPILL_HEAD_ROWS = 50
SPILL_TAIL_ROWS = 10


def _json_size(rows: list) -> int:
    try:
        return len(json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 0


def should_spill(rows: list) -> bool:
    """FR1 threshold: row count OR serialized size."""
    if not rows:
        return False
    return len(rows) > SPILL_MIN_ROWS or _json_size(rows) > SPILL_MIN_BYTES


def head_tail(rows: list, head: int = None, tail: int = None) -> list:
    """FR2 preview: head N + tail M rows (marked so the UI can show the
    omission)."""
    head = head or SPILL_HEAD_ROWS
    tail = tail or SPILL_TAIL_ROWS
    if len(rows) <= head + tail:
        return rows
    out = list(rows[:head])
    out.append({"__omitted__": len(rows) - head - tail})
    out.extend(rows[-tail:])
    return out


async def save_spill(session, session_id: str, sql: str,
                     rows: list) -> Optional[str]:
    """Persist the FULL masked result; returns spill_id or None."""
    if not should_spill(rows):
        return None
    spill_id = f"spill_{uuid.uuid4().hex[:12]}"
    try:
        payload = json.dumps(rows, ensure_ascii=False, default=str)
        await session.execute(text("""
            INSERT INTO data_agent.result_spill
                (spill_id, session_id, sql_hash, sql_text, rows_json,
                 row_count, created_at)
            VALUES (:sid, :ssid, :hash, :stext, :rows, :rc, :ts)
        """), {
            "sid": spill_id, "ssid": session_id,
            "hash": hashlib.md5(sql.encode()).hexdigest()[:16],
            "stext": (sql or "")[:2000],
            "rows": payload, "rc": len(rows), "ts": datetime.now(),
        })
        await session.commit()
        return spill_id
    except Exception as e:
        logger.warning(f"spill save failed (non-fatal): {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        return None


async def get_spill(session, spill_id: str,
                    offset: int = 0, limit: int = 100) -> dict:
    """FR2 paginated fetch of a spilled result."""
    try:
        result = await session.execute(text("""
            SELECT rows_json, row_count, session_id, sql_text, created_at
            FROM data_agent.result_spill WHERE spill_id = :sid
        """), {"sid": spill_id})
        row = result.mappings().first()
        if row is None:
            return {"found": False}
        rows = json.loads(row["rows_json"] or "[]")
        total = int(row["row_count"] or len(rows))
        page = rows[offset:offset + limit]
        return {
            "found": True, "spill_id": spill_id,
            "row_count": total, "offset": offset,
            "rows": page,
        }
    except Exception as e:
        logger.warning(f"spill get failed: {e}")
        return {"found": False, "error": str(e)}


async def delete_expired(session, ttl_hours: int = None) -> int:
    """TTL sweep (FR1: 7 天). Returns rows deleted."""
    hours = ttl_hours if ttl_hours is not None else SPILL_TTL_HOURS
    if hours <= 0:
        return 0
    cutoff = datetime.now() - timedelta(hours=hours)
    try:
        result = await session.execute(text("""
            DELETE FROM data_agent.result_spill WHERE created_at < :cutoff
        """), {"cutoff": cutoff})
        await session.commit()
        return int(result.rowcount or 0)
    except Exception as e:
        logger.warning(f"spill delete_expired failed: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        return 0


async def resolve_full_rows(state: dict, session) -> list:
    """FR3: downstream nodes (decision_insight / code_executor) fetch
    the FULL result when it was spilled, else fall back to the in-state
    truncated rows."""
    spill_id = state.get("_spill_id")
    if spill_id:
        got = await get_spill(session, spill_id, limit=1_000_000)
        if got.get("found"):
            return got["rows"]
    return state.get("_last_result", [])
