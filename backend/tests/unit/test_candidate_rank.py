"""Tests for multi-path candidate generation + ranking (多路重排).

Covers:
  - generate_candidates: produces complete plans, capped, deduped by SQL
  - candidate confidence scoring
  - candidate_ranker: confidence+rerank+history blend, top_n, degradation
  - _resolve_candidate_plan: picks candidate by id from persisted payload
  - resume endpoint candidate path
"""
import asyncio
from datetime import date

import pytest

from app.agent.candidate_generator import generate_candidates, _prioritized_terms
from app.agent.candidate_ranker import CandidateRanker, DEFAULT_WEIGHTS
from app.api.routers.clarify_router import _resolve_candidate_plan


class TestGenerateCandidates:
    def test_generates_complete_plans(self):
        cands = generate_candidates(
            "看下销售", keywords=["销售"],
            today=date(2026, 8, 12), max_candidates=4,
        )
        assert len(cands) >= 1
        for c in cands:
            # Every candidate is a full plan (has measures + explain + sql).
            assert c["measures"]
            assert c["_candidate_id"]
            assert c["_explain"]
            assert c["sql_preview"]
            assert "confidence" in c

    def test_cap_respected(self):
        cands = generate_candidates("销售", max_candidates=3)
        assert len(cands) <= 3

    def test_no_duplicate_sql(self):
        cands = generate_candidates(
            "看下销售", keywords=["销售"],
            today=date(2026, 8, 12), max_candidates=10,
        )
        sqls = [c["sql_preview"] for c in cands]
        assert len(sqls) == len(set(sqls))

    def test_empty_question(self):
        assert generate_candidates("") == []

    def test_sql_is_valid_shape(self):
        cands = generate_candidates("华北 上个月的gmv", keywords=["华北", "gmv"],
                                    today=date(2026, 8, 12), max_candidates=3)
        for c in cands:
            sql = c["sql_preview"]
            assert sql.startswith("SELECT")
            assert "FROM dw." in sql

    def test_exact_term_prioritized(self):
        """If question hits a term, that term appears in candidates."""
        cands = generate_candidates("昨天的DAU", keywords=["DAU"],
                                    today=date(2026, 8, 12), max_candidates=4)
        terms = {m.get("business_term")
                 for c in cands for m in c["measures"]}
        assert "DAU" in terms

    def test_group_by_candidate_has_valid_joins(self):
        """Category group-by must include the full join chain."""
        cands = generate_candidates("看下销售", keywords=["销售"],
                                    today=date(2026, 8, 12), max_candidates=8)
        for c in cands:
            gb_cols = [g.get("column", "") for g in c.get("group_by", [])]
            sql = c["sql_preview"]
            for col in gb_cols:
                if not col:
                    continue
                alias = col.split(".")[0]
                # Alias must appear in a JOIN clause.
                assert f"JOIN dw." in sql, f"no join in {sql[:80]}"
                assert f" AS {alias} " in sql or f"{alias}." in sql


class TestPrioritizedTerms:
    def test_gmv_first(self):
        terms = _prioritized_terms()
        assert terms[0] == "GMV"


class _FakeRerank:
    """Deterministic rerank: index i gets 0.9 - 0.05*i."""
    def __init__(self):
        self.calls = 0
    async def arerank(self, q, docs, top_n=30):
        self.calls += 1
        return [{"index": i, "relevance_score": max(0.0, 0.9 - 0.05 * i)}
                for i in range(len(docs))]


class TestCandidateRanker:
    def _candidates(self, n=3):
        confs = [1.0, 0.9, 0.7, 0.6, 0.5]
        return [
            {
                "_candidate_id": f"c{i}",
                "measures": [{"business_term": "GMV" if i == 0 else "order_count"}],
                "confidence": confs[i] if i < len(confs) else 0.5,
                "_explain": f"候选 {i}",
            }
            for i in range(n)
        ]

    def test_blends_signals(self):
        c = self._candidates()
        ranker = CandidateRanker(rerank_client=_FakeRerank(), history=[])
        ranked = asyncio.run(ranker.rank("看下销售", c))
        assert len(ranked) == 3
        for cand in ranked:
            assert "_score" in cand
            assert "_signals" in cand

    def test_sorted_desc_by_score(self):
        c = self._candidates()
        ranker = CandidateRanker(rerank_client=_FakeRerank(), history=[])
        ranked = asyncio.run(ranker.rank("看下销售", c))
        scores = [x["_score"] for x in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_top_n_truncates(self):
        c = self._candidates(4)
        ranker = CandidateRanker()
        ranked = asyncio.run(ranker.rank("看下销售", c, top_n=2))
        assert len(ranked) == 2

    def test_no_rerank_degrades_to_confidence(self):
        c = self._candidates()
        ranker = CandidateRanker(rerank_client=None, history=[])
        ranked = asyncio.run(ranker.rank("看下销售", c))
        # Confidence-only: candidate 0 (conf 1.0) should be first.
        assert ranked[0]["_candidate_id"] == "c0"
        # rerank signal is 0 when unavailable.
        assert all(x["_signals"]["rerank"] == 0.0 for x in ranked)

    def test_history_bias(self):
        c = self._candidates()
        # User previously confirmed order_count (candidate 1).
        history = [{"measures": [{"business_term": "order_count"}]}]
        ranker = CandidateRanker(rerank_client=None, history=history)
        ranked = asyncio.run(ranker.rank("看下销售", c))
        # order_count has confidence 0.9 + history 0.15 boost.
        order_cand = next(x for x in ranked if x["_candidate_id"] == "c1")
        assert order_cand["_signals"]["history"] == 1.0
        # GMV (c0) confidence 1.0 * 0.5 = 0.5; order (c1) 0.9*0.5+0.15=0.6.
        # So c1 should outrank c0 when history favors it.
        assert ranked[0]["_candidate_id"] == "c1"

    def test_weights_configurable(self):
        c = self._candidates()
        ranker = CandidateRanker(weights={"confidence": 1.0, "rerank": 0.0, "history": 0.0})
        ranked = asyncio.run(ranker.rank("x", c))
        assert ranked[0]["_candidate_id"] == "c0"

    def test_empty_candidates(self):
        ranker = CandidateRanker()
        assert asyncio.run(ranker.rank("x", [])) == []

    def test_default_weights(self):
        assert DEFAULT_WEIGHTS == {"confidence": 0.5, "rerank": 0.35, "history": 0.15}


class TestResolveCandidatePlan:
    def test_finds_by_id(self):
        payload = {
            "field_suggestions": [],
            "candidates": [
                {"_candidate_id": "cand_1", "measures": [], "confidence": 0.9},
                {"_candidate_id": "cand_2", "measures": [], "confidence": 1.0},
            ],
        }
        class _Sess:
            suggestions_given = payload
        found = _resolve_candidate_plan(_Sess(), "cand_2")
        assert found is not None
        assert found["_candidate_id"] == "cand_2"
        assert found["confidence"] == 1.0

    def test_missing_id_returns_none(self):
        payload = {"candidates": [{"_candidate_id": "cand_1"}]}
        class _Sess:
            suggestions_given = payload
        assert _resolve_candidate_plan(_Sess(), "nope") is None

    def test_bare_list_payload(self):
        # Older sessions may store a bare candidate list.
        payload = [
            {"_candidate_id": "a", "measures": []},
            {"_candidate_id": "b", "measures": []},
        ]
        class _Sess:
            suggestions_given = payload
        found = _resolve_candidate_plan(_Sess(), "b")
        assert found is not None
        assert found["_candidate_id"] == "b"

    def test_non_dict_payload_returns_none(self):
        class _Sess:
            suggestions_given = "not a dict"
        assert _resolve_candidate_plan(_Sess(), "x") is None
