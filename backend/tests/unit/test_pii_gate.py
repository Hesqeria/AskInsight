"""PII gate tests.

Covers:
  - extract_referenced_columns over various SQL shapes
  - check_pii_violations: PII columns trigger; non-PII columns pass
  - inheritance: a column on a low-PII class inherits ancestor's level
  - empty/missing ontology -> no violations (graceful degrade)
  - correct_sql node: sets state.needs_approval correctly
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.pii import (
    extract_referenced_columns,
    check_pii_violations,
    PiiViolation,
    DEFAULT_PII_APPROVAL_THRESHOLD,
)
from app.ontology import (
    ClassNode, RelationEdge, InstanceRef,
)


# --------------------------------------------------------------------------- #
# extract_referenced_columns
# --------------------------------------------------------------------------- #
def test_extract_simple_alias():
    sql = "SELECT u.user_id FROM dim_user_info u WHERE u.phone = 'x'"
    refs = extract_referenced_columns(sql)
    # `u` is an alias for dim_user_info; resolved through the FROM map.
    assert ("dim_user_info", "user_id") in refs
    assert ("dim_user_info", "phone") in refs


def test_extract_unaliased_table():
    sql = "SELECT user_id FROM dim_user_info WHERE phone = 'x'"
    refs = extract_referenced_columns(sql)
    # No alias - references like `dim_user_info.col` would be caught; bare
    # `user_id` is not a `<t>.<c>` so it's not in the output (the FROM
    # clause alone doesn't reference a column).
    assert refs == []  # bare columns aren't `<t>.<c>` form


def test_extract_dedupes_repeated_refs():
    sql = ("SELECT u.user_id, u.phone_num FROM dim_user_info u "
           "WHERE u.user_id > 0 GROUP BY u.user_id")
    refs = extract_referenced_columns(sql)
    # Each (table, column) appears once even though user_id shows up 3x.
    keys = set(refs)
    assert keys == {("dim_user_info", "user_id"), ("dim_user_info", "phone_num")}


def test_extract_skips_sql_keywords():
    sql = "SELECT u.user_id FROM dim_user_info u ORDER BY u.user_id"
    refs = extract_referenced_columns(sql)
    # ORDER should not be treated as a table reference.
    assert ("ORDER", "BY") not in refs
    assert all(t.lower() != "order" for t, _ in refs)


def test_extract_handles_schema_qualified_table():
    """Schema-qualified FROM tables resolve aliases correctly."""
    sql = ("SELECT u.user_id FROM dw.dim_user_info u "
           "WHERE u.phone_num LIKE '138%'")
    refs = extract_referenced_columns(sql)
    assert ("dim_user_info", "user_id") in refs
    assert ("dim_user_info", "phone_num") in refs


def test_extract_empty_sql():
    assert extract_referenced_columns("") == []
    assert extract_referenced_columns(None) == []


def test_extract_handles_joins():
    sql = """
        SELECT o.order_id, u.user_id
        FROM dwd_order_info_inc o
        JOIN dim_user_info u ON u.user_id = o.user_id
    """
    refs = extract_referenced_columns(sql)
    assert ("dwd_order_info_inc", "order_id") in refs
    assert ("dim_user_info", "user_id") in refs


# --------------------------------------------------------------------------- #
# check_pii_violations
# --------------------------------------------------------------------------- #
def _fake_repo(classes, instances, relations=None):
    """Build an AsyncMock OntologyRepository with controlled data."""
    repo = AsyncMock()
    repo.list_classes.return_value = classes
    repo.list_instances.return_value = instances
    repo.list_relations.return_value = relations or []
    return repo


def _seed():
    """Mirror the PRD seed: User is pii=3, MemberLevel inherits from User."""
    classes = [
        ClassNode("C001", "Entity",      "实体",     None,   False, 0),
        ClassNode("C011", "User",        "用户",     "C001", False, 3),  # high PII
        ClassNode("C012", "MemberLevel", "会员等级", "C011", False, 0),  # inherits 3
        ClassNode("C022", "SKU",         "SKU",      "C001", False, 0),  # no PII
    ]
    relations = []  # parent_class_id chain already encodes hierarchy
    instances = [
        # Bare table.column form (the parser produces this shape).
        InstanceRef("dw.dim_user_info.user_id",      "dw", "dim_user_info",      "user_id",     "C011"),
        InstanceRef("dw.dim_user_info.phone_num",    "dw", "dim_user_info",      "phone_num",   "C011"),
        InstanceRef("dw.dim_user_info.member_level", "dw", "dim_user_info",      "member_level","C012"),
        InstanceRef("dw.dim_sku_info.sku_id",        "dw", "dim_sku_info",       "sku_id",      "C022"),
    ]
    return classes, instances, relations


@pytest.mark.asyncio
async def test_pii_violation_on_user_column():
    classes, instances, relations = _seed()
    repo = _fake_repo(classes, instances, relations)
    sql = "SELECT u.user_id FROM dim_user_info u"
    violations = await check_pii_violations(sql, repo, threshold=3)
    assert len(violations) == 1
    v = violations[0]
    assert v.class_id == "C011"
    assert v.effective_pii_level == 3
    assert v.column_ref == "dw.dim_user_info.user_id"


@pytest.mark.asyncio
async def test_pii_inheritance_member_level():
    """MemberLevel class itself is pii=0, but it inherits from User (pii=3)."""
    classes, instances, relations = _seed()
    repo = _fake_repo(classes, instances, relations)
    sql = "SELECT u.member_level FROM dim_user_info u"
    violations = await check_pii_violations(sql, repo, threshold=3)
    assert len(violations) == 1
    assert violations[0].class_id == "C012"
    assert violations[0].own_pii_level == 0          # before inheritance
    assert violations[0].effective_pii_level == 3    # after inheritance


@pytest.mark.asyncio
async def test_pii_no_violation_on_low_pii_column():
    classes, instances, relations = _seed()
    repo = _fake_repo(classes, instances, relations)
    sql = "SELECT s.sku_id FROM dim_sku_info s"
    violations = await check_pii_violations(sql, repo, threshold=3)
    assert violations == []


@pytest.mark.asyncio
async def test_pii_dedupes_same_class():
    """Multiple columns from the same class produce ONE violation."""
    classes, instances, relations = _seed()
    repo = _fake_repo(classes, instances, relations)
    sql = ("SELECT u.user_id, u.phone_num FROM dim_user_info u")
    violations = await check_pii_violations(sql, repo, threshold=3)
    # Both columns are on C011 -> one violation entry.
    assert len(violations) == 1


@pytest.mark.asyncio
async def test_pii_empty_ontology_returns_empty():
    """When the ontology tables are empty (DDL not applied), no violations."""
    repo = _fake_repo([], [], [])
    sql = "SELECT u.user_id FROM dim_user_info u"
    violations = await check_pii_violations(sql, repo, threshold=3)
    assert violations == []


@pytest.mark.asyncio
async def test_pii_repo_exception_returns_empty():
    """DB errors degrade to "no violation" (pipeline stays healthy)."""
    repo = AsyncMock()
    repo.list_classes.side_effect = RuntimeError("DB down")
    sql = "SELECT u.user_id FROM dim_user_info u"
    violations = await check_pii_violations(sql, repo, threshold=3)
    assert violations == []


@pytest.mark.asyncio
async def test_pii_respects_threshold():
    """threshold=2 should also flag pii_level=2 columns."""
    classes = [
        ClassNode("C040", "Payment", "支付", None, False, 2),  # medium PII
    ]
    instances = [
        InstanceRef("dw.dwd_payment.user_id", "dw", "dwd_payment", "user_id", "C040"),
    ]
    repo = _fake_repo(classes, instances)
    sql = "SELECT p.user_id FROM dwd_payment p"

    # threshold=3 -> no violation (Payment is only 2).
    assert await check_pii_violations(sql, repo, threshold=3) == []
    # threshold=2 -> violation.
    out = await check_pii_violations(sql, repo, threshold=2)
    assert len(out) == 1
    assert out[0].effective_pii_level == 2


def _patch_correct_sql_chain(monkeypatch, response_text):
    """Stub the LCEL chain `prompt | llm | StrOutputParser` used inside
    correct_sql. Replaces `llm` with a sync RunnableLambda that returns
    a real AIMessage - StrOutputParser requires a BaseMessage, not a
    raw string or MagicMock."""
    from app.agent.nodes import correct_sql as mod
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    response = AIMessage(content=response_text)
    def _fake_invoke(_input, config=None):
        return response
    runnable = RunnableLambda(_fake_invoke)
    monkeypatch.setattr(mod, "llm", runnable)


def _make_runtime(context: dict):
    runtime = MagicMock()
    runtime.stream_writer = lambda x: None
    runtime.context = context
    return runtime


@pytest.mark.asyncio
async def test_correct_sql_sets_needs_approval_on_pii(monkeypatch):
    """When the corrected SQL references a PII L3 column, the node must
    set state.needs_approval=True with a populated approval_reason."""
    from app.agent.nodes import correct_sql as mod
    _patch_correct_sql_chain(monkeypatch, "SELECT u.user_id FROM dim_user_info u")

    # Stub the ontology repo (via meta_doris_repository context slot).
    classes, instances, relations = _seed()
    fake_repo = MagicMock()
    # _coerce_ontology_repo needs session to be non-None to rebuild.
    fake_repo.session = "fake-session-handle"
    # The coerced OntologyRepository uses .list_classes/.list_instances:
    coerced = AsyncMock()
    coerced.list_classes.return_value = classes
    coerced.list_instances.return_value = instances
    coerced.list_relations.return_value = relations
    monkeypatch.setattr(
        "app.repositories.doris.ontology.ontology_repository.OntologyRepository",
        lambda session: coerced,
    )

    state = {
        "sql": "SELECT bad FROM x",
        "query": "test",
        "table_infos": [], "metric_infos": [],
        "date_info": {}, "db_info": {}, "error": "syntax error",
    }
    runtime = _make_runtime({"meta_doris_repository": fake_repo})

    result = await mod.correct_sql(state, runtime)
    assert result["needs_approval"] is True
    assert "user_id" in result["approval_reason"] or "C011" in result["approval_reason"]


@pytest.mark.asyncio
async def test_correct_sql_passes_when_no_pii(monkeypatch):
    """Corrected SQL on a non-PII table -> needs_approval=False."""
    from app.agent.nodes import correct_sql as mod
    _patch_correct_sql_chain(monkeypatch, "SELECT s.sku_id FROM dim_sku_info s")

    classes, instances, relations = _seed()
    coerced = AsyncMock()
    coerced.list_classes.return_value = classes
    coerced.list_instances.return_value = instances
    coerced.list_relations.return_value = relations
    fake_repo = MagicMock()
    fake_repo.session = "fake-session-handle"
    monkeypatch.setattr(
        "app.repositories.doris.ontology.ontology_repository.OntologyRepository",
        lambda session: coerced,
    )

    state = {
        "sql": "SELECT s.sku_id FROM dim_sku_info s",
        "query": "test", "table_infos": [], "metric_infos": [],
        "date_info": {}, "db_info": {}, "error": "",
    }
    runtime = _make_runtime({"meta_doris_repository": fake_repo})

    result = await mod.correct_sql(state, runtime)
    assert result["needs_approval"] is False
    assert result["approval_reason"] == ""


@pytest.mark.asyncio
async def test_correct_sql_skips_pii_when_no_repo(monkeypatch):
    """Without an ontology repo wired, the gate must degrade to
    needs_approval=False (pipeline keeps flowing)."""
    from app.agent.nodes import correct_sql as mod
    _patch_correct_sql_chain(monkeypatch, "SELECT u.user_id FROM dim_user_info u")

    state = {
        "sql": "SELECT u.user_id FROM dim_user_info u",
        "query": "test", "table_infos": [], "metric_infos": [],
        "date_info": {}, "db_info": {}, "error": "",
    }
    # No meta_doris_repository in context.
    runtime = _make_runtime({})

    from app.agent.nodes import correct_sql as mod
    result = await mod.correct_sql(state, runtime)
    assert result["needs_approval"] is False
    assert result["approval_reason"] == ""


@pytest.mark.asyncio
async def test_correct_sql_empty_input_skips_everything():
    from app.agent.nodes import correct_sql as mod
    state = {"sql": "", "query": "x"}
    runtime = _make_runtime({})
    result = await mod.correct_sql(state, runtime)
    assert result["needs_approval"] is False
    assert result["sql"] == ""
