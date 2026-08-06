"""LRU TTL cache with async locks and deterministic hashing."""

import asyncio
import hashlib
import json
import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, maxsize: int = 100, ttl: int = 600):
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache: OrderedDict = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key):
        async with self._lock:
            if key in self._cache:
                val, ts = self._cache[key]
                if time.time() - ts < self.ttl:
                    self._cache.move_to_end(key)
                    return val
                del self._cache[key]
            return None

    async def set(self, key, val):
        async with self._lock:
            self._cache[key] = (val, time.time())
            self._cache.move_to_end(key)
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    async def clear(self):
        async with self._lock:
            self._cache.clear()


def _stable_hash(key: str) -> str:
    return hashlib.md5(str(key).encode()).hexdigest()


query_cache = TTLCache(maxsize=50, ttl=600)


class RedisCache:
    """Redis cache with deterministic hashing and async-safe fallback."""

    def __init__(self, prefix: str = "cache:", ttl: int = 600):
        self.prefix = prefix
        self.ttl = ttl
        self._fallback = TTLCache(maxsize=50, ttl=ttl)

    async def get(self, key: str):
        from app.clients.redis_client_manager import redis_client_manager
        cache_key = f"{self.prefix}{_stable_hash(key)}"
        try:
            if redis_client_manager.client:
                val = await redis_client_manager.client.get(cache_key)
                if val:
                    return json.loads(val)
            return await self._fallback.get(key)
        except Exception:
            return await self._fallback.get(key)

    async def set(self, key: str, val):
        from app.clients.redis_client_manager import redis_client_manager
        cache_key = f"{self.prefix}{_stable_hash(key)}"
        try:
            if redis_client_manager.client:
                serialized = json.dumps(val, ensure_ascii=False)
                if len(serialized) < 1_000_000:
                    await redis_client_manager.client.setex(cache_key, self.ttl, serialized)
                return
            await self._fallback.set(key, val)
        except Exception:
            await self._fallback.set(key, val)


redis_cache = RedisCache(prefix="da:", ttl=600)
