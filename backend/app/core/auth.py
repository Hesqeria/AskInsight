"""JWT authentication + Redis session + rate limiting."""
import os
import time
import uuid
import jwt
from fastapi import HTTPException, Request

from app.clients.redis_client_manager import redis_client_manager

# Built-in user (enterprise intranet can be changed to query MySQL)
USERS = {
    "admin": {"password": os.getenv("ADMIN_PASSWORD", "admin123"), "role": "admin"},
}
JWT_SECRET = os.getenv("JWT_SECRET", "data-agent-secret-change-me")
JWT_EXPIRE = 86400  # 24h
RATE_LIMIT = 10  # Max 10 requests per minute


async def create_token(username: str) -> str:
    payload = {
        "sub": username,
        "role": USERS.get(username, {}).get("role", "user"),
        "exp": int(time.time()) + JWT_EXPIRE,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    # Store in Redis session
    await redis_client_manager.client.setex(
        f"session:{payload['jti']}", JWT_EXPIRE, username
    )
    return token


async def verify_token(request: Request) -> dict:
    """FastAPI dependency: extract and verify JWT from Header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authentication token")
    token = auth[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token")

    # Redis session validation
    session = redis_client_manager.client.get(f"session:{payload.get('jti')}")
    if not session:
        raise HTTPException(status_code=401, detail="session expired")

    # Rate limiting
    rate_key = f"rate:{payload['sub']}"
    count = await redis_client_manager.client.incr(rate_key)
    if count == 1:
        await redis_client_manager.client.expire(rate_key, 60)
    if count > RATE_LIMIT:
        raise HTTPException(status_code=429, detail=f"Too many requests (limit {RATE_LIMIT}/min)")

    return payload
