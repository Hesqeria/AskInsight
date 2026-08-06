"""JWT authentication + bcrypt password hashing + Redis session + rate limiting."""
import os
import time
import uuid
import sys
import bcrypt
import jwt
from fastapi import HTTPException, Request

from app.clients.redis_client_manager import redis_client_manager

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
    print("INFO: Generated bcrypt hash from ADMIN_PASSWORD", file=sys.stderr)

USERS = {
    "admin": {"password_hash": ADMIN_PASSWORD_HASH, "role": "admin"},
}
JWT_EXPIRE = 86400
RATE_LIMIT = 10


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


async def create_token(username: str) -> str:
    payload = {
        "sub": username,
        "role": USERS.get(username, {}).get("role", "user"),
        "exp": int(time.time()) + JWT_EXPIRE,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    session_key = f"session:{payload['jti']}"
    await redis_client_manager.client.setex(session_key, JWT_EXPIRE, username)
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
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    jti = payload.get("jti")
    if jti:
        session = await redis_client_manager.client.get(f"session:{jti}")
        if not session:
            raise HTTPException(status_code=401, detail="Session expired")

    rate_key = f"rate:{payload['sub']}"
    count = await redis_client_manager.client.incr(rate_key)
    if count == 1:
        await redis_client_manager.client.expire(rate_key, 60)
    if count > RATE_LIMIT:
        raise HTTPException(status_code=429, detail=f"Too many requests (limit {RATE_LIMIT}/min)")

    return payload
