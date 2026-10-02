"""Guard the filter-injection edge cases adopted from competitor issues.

- trailing semicolon (WrenAI #2740 class)
- CTE-leading SQL filter misplacement (SuperSonic #2427 class)
- LIMIT-then-WHERE ordering (SQLBot #1202 class)
"""
import pytest
from app.services.query_service import _find_toplevel_limit_idx


def test_toplevel_limit_found():
    sql = "SELECT a FROM t WHERE dt >= '2026-08-01' LIMIT 1000"
    i = _find_toplevel_limit_idx(sql)
    assert i == sql.upper().index("LIMIT")


def test_inner_limit_ignored():
    sql = ("SELECT t.a FROM (SELECT a FROM o LIMIT 5) t "
           "ORDER BY t.a LIMIT 100")
    i = _find_toplevel_limit_idx(sql)
    assert i == sql.upper().rindex("LIMIT")


def test_limit_inside_string_ignored():
    sql = "SELECT 'LIMIT 5' AS c FROM t"
    assert _find_toplevel_limit_idx(sql) == -1


@pytest.mark.asyncio
async def test_filtered_sql_inserts_before_limit(monkeypatch):
    """No WHERE / ORDER BY: filter must land BEFORE LIMIT, never after."""
    from app.services import query_service as qs

    captured = {}

    class _FakeRepo:
        async def execute_sql(self, sql):
            captured["sql"] = sql
            return []

    svc = qs.QueryService.__new__(qs.QueryService)
    svc.dw_doris_repository = _FakeRepo()
    monkeypatch.setattr(qs, "validate_sql_safety",
                        lambda sql: (True, "ok"))
    await svc.execute_filtered_sql(
        "SELECT a FROM t LIMIT 10;",
        [{"field": "region", "operator": "eq", "value": "华东"}])
    out = captured["sql"]
    assert "WHERE" in out.upper()
    assert out.upper().index("WHERE") < out.upper().rindex("LIMIT")
    assert not out.rstrip().endswith(";")


@pytest.mark.asyncio
async def test_filtered_sql_cte_wrapped(monkeypatch):
    """CTE-leading SQL must be wrapped, not injected into the CTE body."""
    from app.services import query_service as qs

    captured = {}

    class _FakeRepo:
        async def execute_sql(self, sql):
            captured["sql"] = sql
            return []

    svc = qs.QueryService.__new__(qs.QueryService)
    svc.dw_doris_repository = _FakeRepo()
    monkeypatch.setattr(qs, "validate_sql_safety",
                        lambda sql: (True, "ok"))
    sql = ("WITH base AS (SELECT a FROM o WHERE dt > '2026-08-01') "
           "SELECT a FROM base")
    await svc.execute_filtered_sql(
        sql, [{"field": "region", "operator": "eq", "value": "华北"}])
    out = captured["sql"]
    assert out.upper().startswith("SELECT * FROM (WITH")
    assert "__flt" in out
    assert out.count("WHERE") == 2  # CTE's own + the wrapped one
