"""Redis client manager (C3: redis.asyncio async version)."""
from typing import Optional

# C3-01: use redis.asyncio uniformly, do not mix redis-py
try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None


class RedisClientManager:
    def __init__(self):
        self.client: Optional["aioredis.Redis"] = None

    def init(self):
        if aioredis is None:
            raise RuntimeError("redis.asyncio is not installed")
        import os
        self.client = aioredis.Redis(
            host=os.getenv("REDIS_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            max_connections=20,
        )

    async def close(self):
        if self.client:
            await self.client.aclose()  # C3-02: async close to avoid leaks


redis_client_manager = RedisClientManager()
