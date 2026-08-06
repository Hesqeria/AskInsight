"""Phase 1.5 boundary tests: auth module P15-01~P15-04"""
import time
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException


def _make_request(token=None):
    req = MagicMock()
    if token:
        req.headers = {"Authorization": f"Bearer {token}"}
    else:
        req.headers = {}
    return req


# === P15-01: expired token is rejected ===
@pytest.mark.asyncio
async def test_p1501_expired_token_rejected():
    import jwt as jwt_lib
    from app.core.auth import JWT_SECRET
    # build an already-expired token
    expired = jwt_lib.encode(
        {"sub": "admin", "exp": int(time.time()) - 100, "jti": "x"},
        JWT_SECRET, algorithm="HS256"
    )
    from app.core.auth import verify_token
    with pytest.raises(HTTPException) as e:
        await verify_token(_make_request(expired))
    assert e.value.status_code == 401


# === P15-02: wrong password login does not leak internal errors ===
def test_p1502_wrong_password():
    from app.api.routers.auth_router import login, LoginSchema
    import asyncio
    schema = LoginSchema(username="admin", password="wrong")
    result = asyncio.run(login(schema))
    assert "error" in result or "token" not in result


# === P15-03: JWT secret is non-empty and not default (check for hardcoded weak keys) ===
def test_p1503_jwt_secret_strength():
    from app.core.auth import JWT_SECRET
    assert len(JWT_SECRET) >= 16, "JWT secret too short"
    # weak-key check (should not be 'secret'/'key'/'123456' etc.)
    weak = ['secret', 'key', '123456', 'password', 'admin']
    assert JWT_SECRET.lower() not in weak, "JWT secret is a weak key"


# === P15-04: rate-limit window boundary (simulated) ===
def test_p1504_rate_limit_window():
    from app.core.auth import RATE_LIMIT
    assert RATE_LIMIT > 0
    # rate-limit logic lives on the Redis side; here we verify the config is reasonable
    assert RATE_LIMIT <= 100, "rate-limit threshold too large"
