"""Rerank (second-stage cross-encoder) integration tests.

Covers the Bailian gte-rerank-v2 wiring in `merge_retrieved_info`:
- `_rerank_candidates` reorders by relevance score
- Threshold filters out low-score docs
- Tail (RRF candidates the reranker dropped) is preserved
- Empty/failed rerank falls back to RRF order
- `_column_to_rerank_text` serialization shape
"""
import asyncio

import pytest


def _make_col(cid, name, desc="", alias=None, examples=None,
              table_id="t1", typ="BIGINT", role="measure"):
    return {
        "id": cid, "name": name, "type": typ, "role": role,
        "description": desc, "alias": alias or [], "examples": examples or [],
        "table_id": table_id,
    }


def test_column_to_rerank_text_shape():
    from app.agent.nodes.merge_retrieved_info import _column_to_rerank_text
    col = _make_col("c1", "gmv", desc="成交金额", alias=["GMV", "gmv_amt"],
                    examples=["123.45", "678"])
    txt = _column_to_rerank_text(col)
    # name first
    assert txt.startswith("gmv")
    # alias joined
    assert "别名: GMV/gmv_amt" in txt
    # description included
    assert "成交金额" in txt
    # type/role bracket
    assert "[BIGINT/measure]" in txt
    # examples capped at 5
    assert "示例:" in txt and "123.45" in txt


def test_column_to_rerank_text_minimal():
    from app.agent.nodes.merge_retrieved_info import _column_to_rerank_text
    # No alias/desc/examples -> just name + type/role bracket.
    txt = _column_to_rerank_text({"name": "dt", "type": "DATE",
                                  "role": "dimension", "alias": [],
                                  "examples": [], "description": ""})
    assert "dt" in txt
    assert "[DATE/dimension]" in txt


def test_rerank_candidates_reorders_by_score(monkeypatch):
    """The cross-encoder re-scores each (query, doc) pair; the node must
    reorder RRF output by relevance_score, not by RRF rank."""
    from app.agent.nodes.merge_retrieved_info import _rerank_candidates

    sorted_ids = ["c1", "c2", "c3"]  # RRF order
    column_data = {
        "c1": _make_col("c1", "gmv"),
        "c2": _make_col("c2", "order_cnt"),
        "c3": _make_col("c3", "region"),
    }

    class FakeRerank:
        async def arerank(self, query, docs, top_n):
            # Pretend doc index 1 (order_cnt) scores highest, then 0, then 2.
            return [
                {"index": 1, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.6},
                {"index": 2, "relevance_score": 0.2},
            ]

    out = asyncio.run(_rerank_candidates(
        "GMV 多少", sorted_ids, column_data, FakeRerank()))
    assert out == ["c2", "c1", "c3"]
    # Rerank score written back into column_data.
    assert column_data["c2"]["_rerank_score"] == 0.95


def test_rerank_candidates_threshold_filters(monkeypatch):
    """Columns below score_threshold should be dropped from the head
    (but still appended to the tail so recall is preserved)."""
    from app.agent.nodes.merge_retrieved_info import _rerank_candidates
    from app.conf.app_config import app_config

    monkeypatch.setattr(app_config.rerank, "score_threshold", 0.5)

    sorted_ids = ["c1", "c2", "c3", "c4"]
    column_data = {cid: _make_col(cid, f"col{cid}") for cid in sorted_ids}

    class FakeRerank:
        async def arerank(self, query, docs, top_n):
            return [
                {"index": 0, "relevance_score": 0.9},  # c1
                {"index": 1, "relevance_score": 0.7},  # c2
                {"index": 2, "relevance_score": 0.1},  # c3 below threshold
                {"index": 3, "relevance_score": 0.2},  # c4 below threshold
            ]

    out = asyncio.run(_rerank_candidates(
        "q", sorted_ids, column_data, FakeRerank()))
    # Head: only above-threshold (c1, c2).
    # Tail: c3, c4 in original RRF order.
    assert out[:2] == ["c1", "c2"]
    assert sorted(out[2:]) == ["c3", "c4"]


def test_rerank_candidates_falls_back_on_exception(monkeypatch):
    """When the reranker raises, the node must return the original RRF
    order unchanged so the pipeline keeps running."""
    from app.agent.nodes.merge_retrieved_info import _rerank_candidates

    sorted_ids = ["c1", "c2"]
    column_data = {cid: _make_col(cid, f"col{cid}") for cid in sorted_ids}

    class Boom:
        async def arerank(self, *a, **k):
            raise RuntimeError("dashscope down")

    out = asyncio.run(_rerank_candidates(
        "q", sorted_ids, column_data, Boom()))
    assert out == sorted_ids


def test_rerank_candidates_falls_back_when_client_none():
    """No rerank_client wired -> return RRF order as-is."""
    from app.agent.nodes.merge_retrieved_info import _rerank_candidates
    out = asyncio.run(_rerank_candidates(
        "q", ["c1", "c2"], {"c1": _make_col("c1", "x")}, None))
    assert out == ["c1", "c2"]


def test_rerank_candidates_empty_query_returns_rrf():
    from app.agent.nodes.merge_retrieved_info import _rerank_candidates

    class ShouldNotBeCalled:
        async def arerank(self, *a, **k):
            raise AssertionError("rerank must be skipped for empty query")

    out = asyncio.run(_rerank_candidates(
        "", ["c1", "c2"], {"c1": _make_col("c1", "x")}, ShouldNotBeCalled()))
    assert out == ["c1", "c2"]


def test_rerank_candidates_caps_top_n(monkeypatch):
    """top_n from config must be respected (with the hard cap)."""
    from app.agent.nodes.merge_retrieved_info import (
        _rerank_candidates, RERANK_MAX_CANDIDATES,
    )
    from app.conf.app_config import app_config

    monkeypatch.setattr(app_config.rerank, "top_n", 2)

    sorted_ids = ["c1", "c2", "c3", "c4"]
    column_data = {cid: _make_col(cid, f"col{cid}") for cid in sorted_ids}

    captured = {}

    class FakeRerank:
        async def arerank(self, query, docs, top_n):
            captured["docs_len"] = len(docs)
            captured["top_n"] = top_n
            return [{"index": i, "relevance_score": 0.9 - i * 0.1}
                    for i in range(len(docs))]

    out = asyncio.run(_rerank_candidates(
        "q", sorted_ids, column_data, FakeRerank()))
    # Only the first 2 RRF candidates were sent to the reranker.
    assert captured["docs_len"] == 2
    # Head = reranked first 2, tail = remaining 2 in RRF order.
    assert out[:2] == ["c1", "c2"]
    assert sorted(out[2:]) == ["c3", "c4"]


def test_rerank_candidates_preserves_tail_uniqueness():
    """Reranker returning fewer results than candidates must not lose
    any RRF candidate (tail preserves everything the rerank dropped)."""
    from app.agent.nodes.merge_retrieved_info import _rerank_candidates

    sorted_ids = ["c1", "c2", "c3", "c4"]
    column_data = {cid: _make_col(cid, f"col{cid}") for cid in sorted_ids}

    class FakeRerank:
        async def arerank(self, query, docs, top_n):
            # Only returns 1 of the 4 candidates.
            return [{"index": 2, "relevance_score": 0.9}]

    out = asyncio.run(_rerank_candidates(
        "q", sorted_ids, column_data, FakeRerank()))
    assert out[0] == "c3"  # rerank winner
    # The other three are appended in original RRF order, no dupes.
    assert sorted(out[1:]) == ["c1", "c2", "c4"]
    assert len(out) == len(set(out)) == 4
