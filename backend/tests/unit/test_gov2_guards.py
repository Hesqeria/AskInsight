"""PRD v2.0 SS3.4 guard rules (G005/G006/G007/G008 + plumbing)."""
import asyncio
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.guard_chain import (
    limit_rewrite_guard, in_list_guard, join_count_guard,
    select_star_guard, partition_scan_guard, guard_chain,
)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeResult:
    def __init__(self, scalar):
        self._s = scalar
    def scalar(self):
        return self._s


class _FakeSession:
    """Stub for information_schema probes (no live Doris in unit tests)."""
    def __init__(self, scalar):
        self._s = scalar
    async def execute(self, *a, **k):
        return _FakeResult(self._s)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False


def _patch_probe(monkeypatch, scalar):
    import app.clients.doris_client_manager as _dcm
    monkeypatch.setattr(_dcm.doris_client_manager,
                        "session_factory", lambda: _FakeSession(scalar))


class TestLimitRewrite:
    def test_appends_limit(self):
        v = run(limit_rewrite_guard({}, "SELECT x FROM t"))
        assert v is not None and v.sql.endswith("LIMIT 10000")
        assert v.guard == "limit_rewrite"

    def test_existing_limit_skipped(self):
        assert run(limit_rewrite_guard({}, "SELECT x FROM t LIMIT 5")) is None

    def test_trailing_semicolon(self):
        v = run(limit_rewrite_guard({}, "SELECT x FROM t;"))
        assert v.sql == "SELECT x FROM t LIMIT 10000"


class TestInList:
    def test_small_in_passes(self):
        assert run(in_list_guard({}, "SELECT 1 FROM t WHERE x IN (1,2,3)")) is None

    def test_big_in_blocks(self):
        sql = "SELECT 1 FROM t WHERE x IN (" + ",".join(["1"] * 1001) + ")"
        v = run(in_list_guard({}, sql))
        assert v is not None and v.guard == "in_list"


class TestJoinCount:
    def test_two_joins_pass(self):
        assert run(join_count_guard(
            {}, "SELECT 1 FROM a JOIN b ON a.x=b.x JOIN c ON b.y=c.y")) is None

    def test_three_joins_asks(self):
        v = run(join_count_guard(
            {}, "SELECT 1 FROM a JOIN b ON a.x=b.x JOIN c ON b.y=c.y "
                "JOIN d ON c.z=d.z"))
        assert v is not None and "G006" in v.reason


class TestSelectStar:
    def test_no_star_passes(self):
        assert run(select_star_guard({}, "SELECT x FROM t")) is None

    def test_star_narrow_table_passes(self, monkeypatch):
        # probe returns 50 cols (<100) -> no ask
        _patch_probe(monkeypatch, 50)
        assert run(select_star_guard({}, "SELECT * FROM dw.dim_date")) is None

    def test_star_wide_table_asks(self, monkeypatch):
        _patch_probe(monkeypatch, 150)
        v = run(select_star_guard({}, "SELECT * FROM dw.some_wide"))
        assert v is not None and "G004" in v.reason


class TestPartitionScan:
    def test_dt_filter_passes_without_probe(self):
        # has_dt + span<=366 short-circuits BEFORE any DB probe
        sql = ("SELECT gmv FROM dw.ads_gmv_total_day "
               "WHERE dt BETWEEN '2026-08-01' AND '2026-08-10'")
        assert run(partition_scan_guard({}, sql)) is None

    def test_span_over_year_asks(self, monkeypatch):
        _patch_probe(monkeypatch, 20_000_000)
        sql = ("SELECT gmv FROM dw.ads_gmv_total_day "
               "WHERE dt BETWEEN '2024-01-01' AND '2026-08-01'")
        v = run(partition_scan_guard({}, sql))
        assert v is not None and "G008" in v.reason

    def test_big_table_no_dt_asks(self, monkeypatch):
        _patch_probe(monkeypatch, 20_000_000)
        sql = "SELECT gmv FROM dw.huge_fact WHERE user_id = 7"
        v = run(partition_scan_guard({}, sql))
        assert v is not None and "G002" in v.reason

    def test_fallback_message_skipped(self):
        sql = "SELECT 'Field does not exist' AS message"
        assert run(partition_scan_guard({"complexity": "fallback"}, sql)) is None


class TestRegistration:
    def test_all_registered(self):
        names = guard_chain.names()
        for g in ("limit_rewrite", "in_list", "join_count",
                  "select_star", "partition_scan"):
            assert g in names
