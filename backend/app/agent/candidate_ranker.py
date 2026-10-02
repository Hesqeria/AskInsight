"""Candidate ranking: confidence + rerank relevance + history preference.

Blends three signals into a single ordering for presenting multi-path
candidate plans to the user (PRD: 多路重排).

    score(c) = w_conf * confidence(c)
             + w_rerank * rerank_score(c)      # if reranker available
             + w_hist * history_pref(c)        # user/global historical picks

Defaults (deterministic, unit-testable):
    w_conf  = 0.5
    w_rerank = 0.35
    w_hist   = 0.15

rerank_score is normalized to [0,1] (relevance_score from the API is
already roughly there; we clamp). history_pref returns 1.0 for the
measure the user most recently confirmed, else 0.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional


DEFAULT_WEIGHTS = {"confidence": 0.5, "rerank": 0.35, "history": 0.15}


class CandidateRanker:
    """Ranks candidate plan dicts by blended relevance."""

    def __init__(self, weights: Optional[dict] = None,
                 rerank_client=None,
                 history: Optional[list[dict]] = None):
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.rerank_client = rerank_client
        # history: list of past confirmed plan dicts (from clarify_feedback)
        self.history = history or []

    # ------------------------------------------------------------------ #
    # Public
    # ------------------------------------------------------------------ #
    async def rank(self, question: str, candidates: list[dict],
                   top_n: Optional[int] = None) -> list[dict]:
        """Rank candidates in-place (adds `_score`) and return sorted
        copies. top_n truncates to the best N."""
        if not candidates:
            return []

        # 1. Rerank scores (async, optional — degrade if unavailable).
        rerank_scores = {}
        if self.rerank_client is not None:
            try:
                docs = [self._doc_for(c) for c in candidates]
                results = await self.rerank_client.arerank(
                    question, docs, top_n=len(docs),
                )
                for r in results:
                    idx = r.get("index")
                    if idx is not None and 0 <= idx < len(candidates):
                        # relevance_score is 0..1-ish; clamp to [0,1].
                        val = float(r.get("relevance_score", 0.0))
                        rerank_scores[idx] = max(0.0, min(1.0, val))
            except Exception:
                # Degrade to confidence-only ordering.
                rerank_scores = {}

        # 2. History preference counts.
        hist_counts = self._history_measure_counts()

        # 3. Blend.
        scored = []
        for idx, c in enumerate(candidates):
            conf = float(c.get("confidence", 0.0) or 0.0)
            rr = rerank_scores.get(idx, 0.0)
            hist = 1.0 if self._measure_of(c) in hist_counts else 0.0
            score = (
                self.weights.get("confidence", 0.5) * conf
                + self.weights.get("rerank", 0.35) * rr
                + self.weights.get("history", 0.15) * hist
            )
            c = dict(c)
            c["_score"] = round(score, 4)
            c["_signals"] = {
                "confidence": round(conf, 3),
                "rerank": round(rr, 3),
                "history": hist,
            }
            scored.append(c)

        scored.sort(key=lambda x: -x["_score"])
        if top_n is not None:
            scored = scored[:top_n]
        return scored

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _doc_for(self, candidate: dict) -> str:
        """Build the rerank document text from the candidate plan."""
        parts = [candidate.get("_explain", "")]
        m = candidate.get("measures") or []
        if m:
            parts.append(f"指标: {m[0].get('business_term', '')}")
        if candidate.get("time"):
            t = candidate["time"]
            parts.append(f"时间: {t.get('start')}~{t.get('end')}")
        gb = candidate.get("group_by") or []
        if gb:
            parts.append(f"分组: {', '.join(g.get('column', '') for g in gb)}")
        return " ".join(parts)

    def _measure_of(self, candidate: dict) -> str:
        m = candidate.get("measures") or []
        return m[0].get("business_term", "") if m else ""

    def _history_measure_counts(self) -> Counter:
        """Count how often each measure appears in the user's history."""
        counts: Counter = Counter()
        for h in self.history:
            m = h.get("measures") or []
            if m:
                counts[m[0].get("business_term", "")] += 1
        return counts
