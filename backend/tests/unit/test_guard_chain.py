"""M6 guard chain tests: registry order, monotonic reject, rewrite,
stage filtering, decision records."""
import asyncio
import pytest

from app.core.guard_chain import (
    GuardChain, GuardReject, GuardAsk, GuardRewrite,
)


def _chain_with(*items):
    c = GuardChain()
    for i, item in enumerate(items):
        if isinstance(item, tuple):
            fn, stage = item
        else:
            fn, stage = item, "pre_validate"
        c.register(f"g{i}", fn, stage=stage)
    return c


async def _ok(state, sql, ctx):
    return None


async def _deny(state, sql, ctx):
    return GuardReject(reason="nope", guard="x")


async def _ask(state, sql, ctx):
    return GuardAsk(reason="needs human", guard="x")


async def _rw(state, sql, ctx):
    return GuardRewrite(sql=sql + " /*sample*/", reason="cost", guard="x")


def test_pass_when_all_pass():
    c = _chain_with(_ok, _ok)
    v = asyncio.run(c.run({}, "SELECT 1", {}))
    assert v.outcome == "pass"
    assert [d["outcome"] for d in v.decisions] == ["pass", "pass"]


def test_monotonic_reject_first_wins():
    """FR3: first reject terminates; later guards never run."""
    ran = []

    async def deny_then_log(state, sql, ctx):
        ran.append("deny")
        return GuardReject(reason="denied", guard="deny")

    async def later(state, sql, ctx):
        ran.append("later")
        return None

    c = _chain_with(deny_then_log, later)
    v = asyncio.run(c.run({}, "SELECT 1", {}))
    assert v.outcome == "reject"
    assert v.reason == "denied"
    assert ran == ["deny"]  # later never executed


def test_reject_not_flipped_by_later_allow():
    """Order-independence: registering the reject last still rejects."""

    async def probe(state, sql, ctx):
        return GuardRewrite(sql="SELECT 2", reason="rw", guard="rw")

    c = _chain_with(probe, _deny)
    v = asyncio.run(c.run({}, "SELECT 1", {}))
    assert v.outcome == "reject"


def test_ask_suspends():
    c = _chain_with(_ok, _ask)
    v = asyncio.run(c.run({}, "SELECT 1", {}))
    assert v.outcome == "ask"
    assert v.asked


def test_rewrite_continues_chain():
    seen_sql = []

    async def probe(state, sql, ctx):
        seen_sql.append(sql)
        return None

    c = _chain_with(_rw, probe)
    v = asyncio.run(c.run({}, "SELECT 1", {}))
    assert v.outcome == "pass"
    assert seen_sql == ["SELECT 1 /*sample*/"]
    assert v.rewrites and v.rewrites[0]["guard"] == "g0"


def test_stage_filter():
    c = GuardChain()
    c.register("pre", _deny, stage="pre_validate")
    c.register("cost", _ok, stage="cost")
    # Running only the cost stage must NOT run the pre reject.
    v = asyncio.run(c.run({}, "SELECT 1", {}, stages=["cost"]))
    assert v.outcome == "pass"
    # Full run hits the reject.
    v2 = asyncio.run(c.run({}, "SELECT 1", {}))
    assert v2.outcome == "reject"


def test_builtin_registration_order():
    """FR2 canonical order is stable."""
    from app.core import guard_chain as mod
    c = mod.guard_chain
    names = c.names()
    assert names.index("forbidden_keywords") < names.index("rbac_tables")
    assert names.index("rbac_tables") < names.index("pii_scan")
    assert names.index("pii_scan") < names.index("cost_estimate")


def test_builtin_keyword_guard():
    from app.core.guard_chain import forbidden_keywords_guard
    v = asyncio.run(forbidden_keywords_guard({}, "DROP TABLE t", {}))
    assert isinstance(v, GuardReject)
    v2 = asyncio.run(forbidden_keywords_guard({}, "SELECT 1", {}))
    assert v2 is None


def test_exception_in_guard_treated_as_skip():
    async def boom(state, sql, ctx):
        raise RuntimeError("guard crash")

    c = _chain_with(boom, _deny)
    v = asyncio.run(c.run({}, "SELECT 1", {}))
    # The crashed guard is skipped; the later deny still applies.
    assert v.outcome == "reject"
    assert v.guard == "g1"


def test_duplicate_name_replaces():
    c = GuardChain()
    c.register("dup", _deny)
    c.register("dup", _ok)
    assert c.names() == ["dup"]
    v = asyncio.run(c.run({}, "SELECT 1", {}))
    assert v.outcome == "pass"
