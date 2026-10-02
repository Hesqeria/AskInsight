"""Pattern grants + role precedence (Cube #11574 / SQLBot #1385 consensus)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.access_control import check_sql_permission


def _repo_with_perms(perms: dict):
    repo = MagicMock()
    repo.session = MagicMock()
    from app.services import access_control as ac
    orig = ac.load_role_permissions
    async def fake_load(session, role):
        return perms
    ac.load_role_permissions = fake_load
    return orig


@pytest.mark.asyncio
async def test_pattern_grant_allows_unlisted_table():
    from app.services import access_control as ac
    orig = _repo_with_perms({"ads_*": 1, "dim_base_province": 1})
    try:
        ok, why = await check_sql_permission(
            None, "SELECT gmv FROM ads_gmv_total_day WHERE dt='2026-08-07'",
            "viewer")
        assert ok, why
    finally:
        ac.load_role_permissions = orig


@pytest.mark.asyncio
async def test_exact_deny_beats_pattern_allow():
    from app.services import access_control as ac
    orig = _repo_with_perms({"ads_*": 1, "ads_secret": 0})
    try:
        ok, why = await check_sql_permission(
            None, "SELECT x FROM ads_secret", "viewer")
        assert not ok and "ads_secret" in why
    finally:
        ac.load_role_permissions = orig


@pytest.mark.asyncio
async def test_ungranted_table_denied():
    from app.services import access_control as ac
    orig = _repo_with_perms({"ads_*": 1})
    try:
        ok, _ = await check_sql_permission(
            None, "SELECT total_amount FROM dw.dwd_order_info_inc", "viewer")
        assert not ok
    finally:
        ac.load_role_permissions = orig


@pytest.mark.asyncio
async def test_admin_bypass():
    ok, why = await check_sql_permission(
        None, "SELECT * FROM anything", "admin")
    assert ok and "bypass" in why
