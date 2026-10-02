"""Data-aligned "today".

The demo warehouse is loaded up to a freeze date; resolving relative
dates (昨天/本月/最近7天) against the REAL today lands in empty windows.
This helper resolves "today" to MAX(dt) of the main fact table instead
(env DATA_TODAY_MODE=real restores wall-clock behavior). Cached 10 min.
"""
import os
import time

from app.core.log import logger

_TTL = 600
_cache = {"date": None, "min": None, "max": None, "ts": 0.0}
_extra: dict = {}


def _mode() -> str:
    return os.getenv("DATA_TODAY_MODE", "freeze")


async def get_data_today(session, expr: str = "dt",
                         table: str = "dw.ads_gmv_total_day",
                         key: str = "default") -> "date | None":
    """Return MAX(expr) of `table` as that timeline's "today" (cached).

    Different business timelines freeze at different dates (demo dw:
    dt=2026-08-07 while create_time=2026-04-30, refund=2026-04-26) -
    callers pass their own column/table so every subject aligns to
    ITS OWN data timeline. Returns None on failure / mode=real.
    """
    import datetime as _dt

    if _mode() != "freeze":
        return None
    now = time.time()
    slot = _extra.setdefault(key, {"date": None, "ts": 0.0})
    if key == "default" and _cache["date"] and now - _cache["ts"] < _TTL:
        return _cache["date"]
    if slot["date"] and now - slot["ts"] < _TTL:
        return slot["date"]
    try:
        from sqlalchemy import text
        safe_expr = expr if expr in ("dt", "create_time", "operate_time",
                                     "payment_time", "done_time") else "dt"
        row = (await session.execute(text(
            f"SELECT MAX(CAST({safe_expr} AS DATE)) FROM {table}"
        ))).fetchone()
        if row and row[0]:
            if key == "default":
                _cache["date"] = row[0]
                _cache["max"] = row[0]
                _cache["ts"] = now
            slot["date"] = row[0]
            slot["ts"] = now
            logger.info(f"data-today[{key}] aligned to {row[0]} ({table}.{safe_expr})")
            return row[0]
    except Exception as e:
        logger.warning(f"data_today lookup failed for {key}: {e}")
    return None


def coverage_text() -> str:
    """Human-readable coverage range for empty-result hints."""
    lo, hi = _cache.get("min"), _cache.get("max")
    if lo and hi:
        return f"warehouse data covers {lo} ~ {hi}"
    return ""
