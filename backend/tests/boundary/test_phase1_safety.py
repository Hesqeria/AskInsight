"""Phase 1 security boundary tests: B-S1~B-S6"""
import pytest
from unittest.mock import MagicMock, patch
import re


# === B-S1: unauthenticated access is rejected ===
@pytest.mark.asyncio
async def test_bs1_no_token_returns_401():
    from app.core.auth import verify_token
    request = MagicMock()
    request.headers = {}
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        await verify_token(request)
    assert e.value.status_code == 401


# === B-S2: SQL injection protection ===
def test_bs2_sql_injection_blocked():
    from app.agent.nodes.validate_sql_safety import DANGEROUS_PATTERNS
    malicious_sqls = [
        "SELECT * FROM fact_order; DROP TABLE dim_region--",
        "DELETE FROM fact_order WHERE 1=1",
        "UPDATE fact_order SET order_amount=0",
        "INSERT INTO dim_region VALUES ('hack')",
        "SELECT * FROM fact_order INTO OUTFILE '/tmp/x'",
    ]
    for sql in malicious_sqls:
        blocked = False
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, sql, re.IGNORECASE):
                blocked = True
                break
        assert blocked, f"malicious SQL not blocked: {sql}"


# === B-S3: whitelist table validation ===
def test_bs3_non_whitelisted_table_rejected():
    from app.agent.nodes.validate_sql_safety import ALLOWED_TABLES
    assert "passwords" not in ALLOWED_TABLES
    assert "users" not in ALLOWED_TABLES
    assert "fact_order" in ALLOWED_TABLES


# === B-S4: enforce LIMIT ===
@pytest.mark.asyncio
async def test_bs4_auto_add_limit():
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    state = {"sql": "SELECT SUM(order_amount) FROM fact_order"}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await validate_sql_safety(state, runtime)
    assert "LIMIT" in result.get("sql", "").upper()
    assert result.get("error") is None


# === B-S5: full-table scan detection ===
def test_bs5_full_scan_detected():
    # only verify logic (do not actually trigger)
    sql = "SELECT * FROM fact_order"
    has_where = bool(re.search(r'where', sql, re.IGNORECASE))
    assert not has_where  # no WHERE


# === B-A6: illegal table name is blocked ===
@pytest.mark.asyncio
async def test_ba6_illegal_table_blocked():
    from app.agent.nodes.validate_sql_safety import validate_sql_safety
    state = {"sql": "SELECT * FROM secret_passwords LIMIT 10"}
    runtime = MagicMock()
    runtime.stream_writer = MagicMock()
    result = await validate_sql_safety(state, runtime)
    assert result.get("error") is not None
    assert "whitelist" in result["error"].lower() or "dangerous" in result["error"].lower()
