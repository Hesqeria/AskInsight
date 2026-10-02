"""M7 approval policy tests."""
import asyncio

from app.services.approval_policy import (
    evaluate, resolve_role, ASK, AUTO_APPROVE, REJECT,
)


def test_never_mode_fail_closed():
    d = evaluate("admin", 1, mode="never")
    assert d.action == REJECT
    assert "never" in d.reason or "严格" in d.reason


def test_admin_auto_approve_in_ask_mode():
    d = evaluate("admin", 5, mode="ask")
    assert d.action == AUTO_APPROVE


def test_l3_within_allowance_auto_approve():
    d = evaluate("L3_engineer", 3, mode="ask")
    assert d.action == AUTO_APPROVE


def test_l3_over_allowance_asks():
    d = evaluate("L3_engineer", 4, mode="ask")
    assert d.action == ASK


def test_unlisted_role_asks():
    d = evaluate("user", 1, mode="ask")
    assert d.action == ASK


def test_zero_violations_unlisted_role_still_asks():
    # Gate only fires when violations exist; policy treats 0 generically.
    d = evaluate("user", 0, mode="ask")
    assert d.action == ASK


def test_resolve_role():
    assert resolve_role("admin") == "admin"
    assert resolve_role("zhangsan") == "user"


def test_wait_approval_policy_wiring_importable():
    import app.agent.nodes.wait_approval  # noqa: F401


def test_expire_pending_disabled_returns_empty():
    from app.services.approval_policy import expire_pending

    class _S:
        async def execute(self, *a, **k):
            raise AssertionError("should not query when disabled")

    out = asyncio.run(expire_pending(_S(), expire_hours=0))
    assert out == []
