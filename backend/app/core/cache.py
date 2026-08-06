"""Simple LRU cache with TTL."""
import time
from collections import OrderedDict
from threading import Lock


class TTLCache:
    def __init__(self, maxsize: int = 100, ttl: int = 600):
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache: OrderedDict = OrderedDict()
        self._lock = Lock()

    def get(self, key):
        with self._lock:
            if key in self._cache:
                val, ts = self._cache[key]
                if time.time() - ts < self.ttl:
                    self._cache.move_to_end(key)
                    return val
                del self._cache[key]
            return None

    def set(self, key, val):
        with self._lock:
            self._cache[key] = (val, time.time())
            self._cache.move_to_end(key)
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self._cache.clear()


# Global cache instance (caches SSE responses, 10 min TTL)
query_cache = TTLCache(maxsize=50, ttl=600)


# C5: Redis cache backend (shared across workers)
class RedisCache:
    """Redis cache, replaces in-process TTLCache (C5-01/02/03)."""
    def __init__(self, prefix: str = "cache:", ttl: int = 600):
        self.prefix = prefix
        self.ttl = ttl
        self._fallback = TTLCache(maxsize=50, ttl=ttl)  # Fallback when Redis is unavailable

    async def get(self, key: str):
        import json
        from app.clients.redis_client_manager import redis_client_manager
        cache_key = f"{self.prefix}{hash(key)}"
        try:
            if redis_client_manager.client:
                val = await redis_client_manager.client.get(cache_key)
                if val:
                    return json.loads(val)
            return self._fallback.get(key)
        except Exception:
            return self._fallback.get(key)

    async def set(self, key: str, val):
        import json
        from app.clients.redis_client_manager import redis_client_manager
        cache_key = f"{self.prefix}{hash(key)}"
        try:
            if redis_client_manager.client:
                # C5-01: Limit val size (SSE max 1MB)
                serialized = json.dumps(val, ensure_ascii=False)
                if len(serialized) < 1_000_000:
                    await redis_client_manager.client.setex(cache_key, self.ttl, serialized)
                return
            self._fallback.set(key, val)
        except Exception:
            self._fallback.set(key, val)

# Global instance
redis_cache = RedisCache(prefix="da:", ttl=600)
