"""Process-level embedding cache.

DashScope embedding calls degrade to seconds-per-text under throttling;
the same business keywords recur across turns, so a process-wide cache
with in-flight dedup removes most of the cost. Also seeds in bulk at
startup via seed_many().
"""
import hashlib
import threading

from app.core.log import logger

_MAX = 1024
_cache = {}
_inflight = set()
_lock = threading.Lock()


def _key(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def cache_size():
    return len(_cache)


async def embed_cached(client, text):
    """Return cached vector for text, fetching once on miss.
    Returns None while another coroutine is fetching the same key
    (caller falls back to a direct call)."""
    k = _key(text)
    if k in _cache:
        return _cache[k]
    with _lock:
        if k in _cache:
            return _cache[k]
        if k in _inflight:
            return None
        _inflight.add(k)
    try:
        vec = await client.aembed_query(text)
        with _lock:
            if len(_cache) >= _MAX:
                _cache.pop(next(iter(_cache)))
            _cache[k] = vec
        return vec
    except Exception:
        logger.warning("embed_cached fetch failed")
        return None
    finally:
        with _lock:
            _inflight.discard(k)


async def warm_many(client, texts) -> int:
    """Batch-embed cache misses in ONE call per chunk (vs one throttled
    call per keyword). Returns number of vectors added."""
    texts = [t for t in dict.fromkeys(t for t in texts if t and t.strip())]
    missing = [t for t in texts if _key(t) not in _cache]
    if not missing:
        return 0
    added = 0
    for i in range(0, len(missing), 16):
        chunk = missing[i:i + 16]
        try:
            vectors = await client.aembed_documents(chunk)
        except Exception:
            continue
        with _lock:
            for t, v in zip(chunk, vectors):
                k = _key(t)
                if k not in _cache and v:
                    if len(_cache) >= _MAX:
                        _cache.pop(next(iter(_cache)))
                    _cache[k] = v
                    added += 1
    return added


async def seed_many(client, texts, batch=16):
    """Warm the cache in bulk (startup). Returns number of new entries."""
    fresh = [t for t in dict.fromkeys(texts) if t and _key(t) not in _cache]
    added = 0
    for i in range(0, len(fresh), batch):
        chunk = fresh[i:i + batch]
        try:
            vectors = await client.aembed_documents(chunk)
        except Exception as e:
            logger.warning(f"embed seed chunk failed: {e}")
            continue
        with _lock:
            for t, v in zip(chunk, vectors):
                if len(_cache) >= _MAX:
                    _cache.pop(next(iter(_cache)))
                _cache[_key(t)] = v
                added += 1
    if added:
        logger.info(f"embed cache seeded: +{added} entries (total {len(_cache)})")
    return added
