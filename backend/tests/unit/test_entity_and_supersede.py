"""utopia #221 entity resolution + open-ontologies #109 supersession."""
import datetime
from unittest.mock import AsyncMock, MagicMock

from app.agent.nodes.semantic_grounding import build_plan
from app.ontology.plan import render_sql_from_plan
from app.services.exemplar_store import add_exemplar, exemplar_id


def test_customer_slice_groups_by_entity_id():
    p = build_plan(question="消费金额最高的前5个客户", keywords=[],
                   today=datetime.date(2026, 8, 7))
    cols = {g.column: g.select for g in p.group_by}
    assert cols.get("u.nick_name") is True
    assert cols.get("u.id") is False  # entity key groups but stays hidden
    sql = render_sql_from_plan(p)
    assert "GROUP BY" in sql and "u.id" in sql
    # hidden key must NOT be selected
    assert "u.id AS" not in sql


def test_gender_slice_unaffected():
    p = build_plan(question="用户数按性别分布", keywords=[],
                   today=datetime.date(2026, 8, 7))
    cols = [(g.column, g.select) for g in p.group_by]
    assert ("u.gender", True) in cols
    assert all(c != "u.id" for c, _ in cols)  # only customer slices need keys


def _fake_session():
    sess = MagicMock()
    captured = []

    async def exec_(stmt, params=None):
        captured.append(str(stmt))
        return MagicMock()

    sess.execute = exec_
    sess.commit = AsyncMock()

    async def rb():
        pass
    sess.rollback = rb
    return sess, captured


import pytest


@pytest.mark.asyncio
async def test_add_exemplar_supersedes_old():
    sess, captured = _fake_session()
    ok1 = await add_exemplar(sess, "昨天GMV", "SELECT 1", "eval_pass")
    ok2 = await add_exemplar(sess, "  昨天gmv  ", "SELECT better",
                             "correct_sql")
    assert ok1 and ok2
    joined = " | ".join(captured)
    assert joined.count("DELETE FROM data_agent.nl2sql_exemplar") == 2
    # same normalized id (case/whitespace only differ) -> supersede works
    assert exemplar_id("昨天GMV") == exemplar_id("  昨天gmv  ")
    inserts = [c for c in captured if "INSERT INTO" in c]
    assert len(inserts) == 2
