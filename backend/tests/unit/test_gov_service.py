"""Unit tests for governance services (GOV-1/2/3/5)."""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services import gov_service  # noqa: E402


class _FakeResult:
    def __init__(self, rows, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


def _session_router(queries):
    """queries: dict sql-fragment -> _FakeResult (matched by substring)."""
    session = MagicMock()

    async def execute(stmt, params=None):
        sql = str(stmt)
        for frag, res in queries.items():
            if frag in sql:
                return res
        return _FakeResult([])

    session.execute = AsyncMock(side_effect=execute)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


# ------------------------------------------------------------------ GOV-1
class TestAssetInventory:
    def test_classify(self):
        assert gov_service._classify("tmp_foo") == "test_artifact"
        assert gov_service._classify("mock_bar") == "test_artifact"
        assert gov_service._classify("dwd_order") == "warehouse_layer"
        assert gov_service._classify("weird_legacy_thing") == "legacy"

    @pytest.mark.asyncio
    async def test_inventory_diff(self, monkeypatch, tmp_path):
        dw = [("t_keep", "c1", 10), ("t_tmp", "", 2), ("t_dim_new", "c", 5)]
        sess = _session_router({
            "FROM information_schema.tables": _FakeResult(dw),
        })
        monkeypatch.setattr(gov_service, "_load_yaml_tables",
                            lambda: {"t_keep": {"role": "fact",
                                                "description": "",
                                                "columns": {}},
                                     "t_gone": {"role": "dim",
                                                "description": "",
                                                "columns": {}}})
        out = await gov_service.asset_inventory(sess)
        assert out["summary"]["registered"] == 1
        assert out["summary"]["unregistered"] == 2
        assert out["summary"]["stale"] == 1
        assert out["summary"]["coverage_pct"] == 33.3
        names = {u["table"] for u in out["unregistered"]["tables"]}
        assert names == {"t_tmp", "t_dim_new"}
        cls = {u["table"]: u["class"] for u in out["unregistered"]["tables"]}
        assert cls["t_tmp"] == "test_artifact"


# ------------------------------------------------------------------ GOV-2
class TestCompliance:
    @pytest.mark.asyncio
    async def test_traffic_light(self, monkeypatch):
        sess = _session_router({
            "information_schema.tables": _FakeResult(
                [("good", "has comment"), ("bad", "")]),
            "GROUP BY table_name": _FakeResult(
                [("good", 4, 4), ("bad", 4, 0)]),
            "FROM data_agent.glossary": _FakeResult([], scalar=10),
        })
        monkeypatch.setattr(gov_service, "_load_yaml_tables", lambda: {
            "good": {"role": "f", "description": "d", "columns": {
                "a": {"role": "m", "description": "x"}}},
            "bad": {"role": "f", "description": "", "columns": {
                "a": {"role": "m", "description": ""}}},
        })
        out = await gov_service.compliance_report(sess)
        lights = {t["table"]: t["light"] for t in out["tables"]}
        assert lights["good"] == "green"
        assert lights["bad"] == "red"
        assert out["traffic_light"]["green"] == 1
        assert out["traffic_light"]["red"] == 1


# ------------------------------------------------------------------ GOV-3
class TestBuildRules:
    def test_rule_kinds(self):
        cols = [
            {"name": "dt", "type": "date", "is_key": False},
            {"name": "gmv", "type": "bigint(20)", "is_key": False},
            {"name": "order_status", "type": "varchar(8)", "is_key": True},
        ]
        rules = gov_service._build_rules("ads_x", cols)
        kinds = {r["rule_type"] for r in rules}
        assert kinds >= {"rowcount", "freshness", "zero_rate",
                         "not_null", "mock_data"}
        # zero_rate only on numeric metric cols
        zr = [r for r in rules if r["rule_type"] == "zero_rate"]
        assert len(zr) == 1 and "gmv" in zr[0]["sql"]
        # freshness uses DATEDIFF (not DATE_DIFF)
        fr = [r for r in rules if r["rule_type"] == "freshness"][0]
        assert "DATEDIFF(CURRENT_DATE()" in fr["sql"]

    def test_non_numeric_metric_not_checked(self):
        cols = [{"name": "gmv_text", "type": "varchar(32)", "is_key": False}]
        rules = gov_service._build_rules("t", cols)
        assert not [r for r in rules if r["rule_type"] == "zero_rate"]

    def test_zero_rate_catches_all_zero(self):
        # pass lambda: all-zero -> 1.0 -> fails
        cols = [{"name": "avg_order_amount", "type": "double", "is_key": False}]
        rule = [r for r in gov_service._build_rules("t", cols)
                if r["rule_type"] == "zero_rate"][0]
        assert rule["pass"](1.0) is False
        assert rule["pass"](0.0) is True


class TestRunQualityChecks:
    @pytest.mark.asyncio
    async def test_run_records_and_classifies(self):
        calls = []

        async def execute(stmt, params=None):
            sql = str(stmt)
            calls.append(sql)
            if "information_schema.columns" in sql:
                return _FakeResult([
                    ("dt", "date", ""), ("gmv", "bigint(20)", "")])
            if "CREATE TABLE" in sql:
                return _FakeResult([])
            if "COUNT(*)" in sql and "IS NULL" not in sql                     and "LIKE" not in sql:
                return _FakeResult([], scalar=7)      # rowcount
            if "DATEDIFF" in sql:
                return _FakeResult([], scalar=1)      # fresh
            if "AVG" in sql:
                return _FakeResult([], scalar=0.05)   # zero rate ok
            return _FakeResult([], scalar=0)

        sess = MagicMock()
        sess.execute = AsyncMock(side_effect=execute)
        sess.commit = AsyncMock()
        sess.rollback = AsyncMock()
        out = await gov_service.run_quality_checks(sess, tables=["ads_t"])
        assert out["checks_total"] == out["checks_passed"]
        assert out["failures"] == []
        assert out["policy"]["fix_action"] == "manual_review"

    @pytest.mark.asyncio
    async def test_failure_never_autofixes(self):
        async def execute(stmt, params=None):
            sql = str(stmt)
            if "information_schema.columns" in sql:
                return _FakeResult([("dt", "date", "")])
            if "CREATE TABLE" in sql:
                return _FakeResult([])
            if "COUNT(*)" in sql:
                return _FakeResult([], scalar=0)      # empty table -> fail
            if "DATEDIFF" in sql:
                return _FakeResult([], scalar=30)     # stale -> fail
            return _FakeResult([], scalar=0)

        sess = MagicMock()
        sess.execute = AsyncMock(side_effect=execute)
        sess.commit = AsyncMock()
        sess.rollback = AsyncMock()
        out = await gov_service.run_quality_checks(sess, tables=["ads_t"])
        assert len(out["failures"]) == 2
        assert out["policy"]["auto_fix"] == "none"
