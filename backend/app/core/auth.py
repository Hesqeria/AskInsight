"""JWT authentication + bcrypt + Redis session (with graceful degradation)."""
import os
import time
import uuid
import sys
import bcrypt
import jwt
import logging
from fastapi import Depends, HTTPException, Request

from app.clients.redis_client_manager import redis_client_manager

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    print("FATAL: JWT_SECRET environment variable is required", file=sys.stderr)
    sys.exit(1)

ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")
if not ADMIN_PASSWORD_HASH:
    plain = os.getenv("ADMIN_PASSWORD")
    if not plain:
        print("FATAL: ADMIN_PASSWORD or ADMIN_PASSWORD_HASH environment variable is required", file=sys.stderr)
        sys.exit(1)
    ADMIN_PASSWORD_HASH = bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

USERS = {"admin": {"password_hash": ADMIN_PASSWORD_HASH, "role": "admin"}}
JWT_EXPIRE = 86400
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "10"))  # 0 = disable

# Lua script for atomic incr + expire (BT-12 race condition fix)
_RATE_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


def verify_password(plain: str, hashed: str) -> bool:
    # bcrypt has 72-byte limit; truncate to avoid ValueError
    return bcrypt.checkpw(plain.encode()[:72], hashed.encode())


def _redis_available() -> bool:
    try:
        return redis_client_manager.client is not None
    except Exception:
        return False


async def create_token(username: str) -> str:
    payload = {
        "sub": username,
        "role": USERS.get(username, {}).get("role", "user"),
        "exp": int(time.time()) + JWT_EXPIRE,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    if _redis_available():
        try:
            session_key = f"session:{payload['jti']}"
            await redis_client_manager.client.setex(session_key, JWT_EXPIRE, username)
        except Exception as e:
            logger.warning(f"Redis session write failed, JWT-only mode: {e}")
    return token


def verify_token_factory(lane: str = "query", limit: int | None = None):
    """Lane-aware auth dependency. Each lane gets its own rate bucket."""
    import functools

    @functools.wraps(verify_token)
    async def _verify(request: Request) -> dict:
        return await _verify_token_impl(request, lane=lane, limit=limit)
    return _verify


async def verify_token(request: Request) -> dict:
    return await _verify_token_impl(request)


async def _verify_token_impl(request: Request, lane: str = "query",
                             limit: int | None = None) -> dict:
    """FastAPI dependency: JWT verify + optional Redis session + rate limit."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authentication token")
    token = auth[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Redis session check (graceful degradation if Redis down)
    jti = payload.get("jti")
    if jti and _redis_available():
        try:
            session = await redis_client_manager.client.get(f"session:{jti}")
            if not session:
                raise HTTPException(status_code=401, detail="Session expired")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Redis session check failed, skipping: {e}")

    # Rate limiting (atomic Lua, graceful degradation)
    if _redis_available():
        try:
            eff_limit = RATE_LIMIT if limit is None else limit
            rate_key = f"rate:{lane}:{payload['sub']}"
            count = await redis_client_manager.client.eval(_RATE_LUA, 1, rate_key, 60)
            if eff_limit and count > eff_limit:
                raise HTTPException(status_code=429, detail=f"Too many requests (limit {eff_limit}/min)")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Redis rate limit failed, allowing request: {e}")

    return payload


async def require_admin(user: dict = Depends(verify_token)) -> dict:
    """FastAPI dependency: verify admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
