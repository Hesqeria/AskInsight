"""Result-level cache for stable queries (OPT-M9).

Caches final structured result (sql + rows) so repeat queries within
24h skip the entire LLM+SQL pipeline.

Policy:
  - Past date ranges (e.g. "2026-07 的 GMV") -> 24h TTL (data stable)
  - Rolling windows (e.g. "最近7天", "今天") -> 60s TTL (data updates daily)
  - No date reference -> 1h TTL default
"""
import hashlib
import json
import re
from typing import Optional

from app.clients.redis_client_manager import redis_client_manager
from app.core.log import logger

CACHE_PREFIX = "nlsql:"
TTL_STABLE = 86400
TTL_ROLLING = 60
TTL_DEFAULT = 3600

_ROLLING_PATTERNS = [
    r"今天", r"昨天", r"本周", r"本月", r"今年",
    r"最近\s*\d+\s*天", r"近\s*\d+\s*天", r"过去\s*\d+",
    r"curdate", r"now\(\)",
]
_PAST_DATE_PATTERNS = [
    r"\d{4}-\d{2}-\d{2}", r"\d{4}年\d{1,2}月",
    r"上个月", r"上月", r"上周", r"去年", r"上季度",
]


def _normalize(q):
    q = re.sub(r"\s+", "", q or "")
    q = re.sub(r"[，。！？、；：,.!?;:（）()【】\[\]]", "", q)
    return q.lower()


def _classify_ttl(question, sql=""):
    text = (question + " " + (sql or "")).lower()
    if any(re.search(p, text) for p in _ROLLING_PATTERNS):
        return TTL_ROLLING
    if any(re.search(p, text) for p in _PAST_DATE_PATTERNS):
        return TTL_STABLE
    return TTL_DEFAULT


def cache_key(question, username=""):
    norm = _normalize(question)
    raw = f"{norm}|{username or 'anon'}"
    return f"{CACHE_PREFIX}{hashlib.md5(raw.encode()).hexdigest()}"


async def get_cached_result(question, username=""):
    if not redis_client_manager.client:
        return None
    try:
        key = cache_key(question, username)
        val = await redis_client_manager.client.get(key)
        if val:
            logger.info(f"NLSQL result cache HIT: {question[:40]}")
            return json.loads(val)
    except Exception as e:
        logger.debug(f"result cache get failed: {e}")
    return None


async def set_cached_result(question, sql, result, username=""):
    if not redis_client_manager.client:
        return
    try:
        key = cache_key(question, username)
        ttl = _classify_ttl(question, sql)
        payload = {"sql": sql, "result": result, "ttl_policy": ttl}
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        if len(serialized) > 2_000_000:
            return
        await redis_client_manager.client.setex(key, ttl, serialized)
        logger.info(f"NLSQL result cached (ttl={ttl}s): {question[:40]}")
    except Exception as e:
        logger.debug(f"result cache set failed: {e}")


async def invalidate_for_question(question, username=""):
    if not redis_client_manager.client:
        return
    try:
        await redis_client_manager.client.delete(cache_key(question, username))
    except Exception:
        pass
