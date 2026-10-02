# -*- coding: utf-8 -*-
"""Recall fusion utilities: RRF and sparse lexical similarity.

Adopted strategies for improving recall in the hybrid RAG:
  1) Reciprocal Rank Fusion (RRF, k=60) to merge dense + sparse lanes
     without score-scale calibration;
  2) character n-gram Jaccard, a language-agnostic sparse scorer that
     works well for short Chinese questions (no tokenizer dependency);
  3) query expansion helper: append ontology alias terms to the raw
     question so dense recall can hit alias-only vocabulary.
"""
import re


def rrf_fuse(rankings: list[list], k: int = 60, weights: list[float] | None = None,
             key=None) -> list:
    """Merge multiple ranked lists via Reciprocal Rank Fusion.

    rankings: list of ranked lists (best first). Items may be any hashable/
    comparable value; duplicates across lanes are merged.
    weights: optional per-lane weights (default all 1.0).
    key: optional key function to map items to a comparable identity.
    Returns items sorted by fused score (desc).
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    scores: dict = {}
    first_seen: dict = {}
    for lane_idx, ranked in enumerate(rankings):
        w = weights[lane_idx] if lane_idx < len(weights) else 1.0
        for rank, item in enumerate(ranked):
            ident = key(item) if key else item
            scores[ident] = scores.get(ident, 0.0) + w / (k + rank + 1)
            if ident not in first_seen:
                first_seen[ident] = item
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    return [first_seen[ident] for ident, _ in ordered]


def char_ngram_similarity(a: str, b: str, n: int = 2) -> float:
    """Dice coefficient over character n-grams (default bigrams).

    Dice (2|A∩B| / (|A|+|B|)) is the standard sparse scorer for short
    Chinese text where word segmentation is unreliable; it is more stable
    than Jaccard when the two strings differ in length
    ("消费金额最高的前3个客户" vs "消费金额前三的客户" ≈ 0.44).
    """
    def grams(s: str) -> set:
        s = re.sub(r"\s+", "", (s or "").lower())
        if len(s) < n:
            return {s} if s else set()
        return {s[i:i + n] for i in range(len(s) - n + 1)}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    if not inter:
        return 0.0
    return 2 * inter / (len(ga) + len(gb))


def expand_with_aliases(question: str, aliases: dict | None = None,
                        limit: int = 4) -> str:
    """Ontology-driven query expansion: append canonical business terms whose
    alias appears in the question (e.g. 营收 -> GMV), so dense recall can
    reach the canonical vocabulary of the semantic layer."""
    if not question or not aliases:
        return question or ""
    q = question.lower()
    extra = []
    for alias, canonical in aliases.items():
        if len(alias) >= 2 and alias.lower() in q and canonical not in extra:
            extra.append(str(canonical))
        if len(extra) >= limit:
            break
    if not extra:
        return question
    return question + " " + " ".join(extra)
