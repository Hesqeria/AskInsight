"""EvoOntology-style alias self-evolution (arXiv 2609.15779 adoption)."""
import datetime
import os
import json
import pytest
from app.agent.nodes.semantic_grounding import build_plan, match_business_terms


def test_revenue_aliases_now_match():
    for q in ("营收是多少", "营业额趋势", "流水多少"):
        assert match_business_terms(q), q


def test_no_measure_mines_candidate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("logs", exist_ok=True)
    p = build_plan(question="净利是多少", keywords=[], today=datetime.date(2026, 8, 7))
    assert not p.measures
    f = tmp_path / "logs" / "alias_candidates.jsonl"
    assert f.exists()
    rec = json.loads(f.read_text(encoding="utf-8").strip())
    assert rec["q"] == "净利是多少" and "like" in rec and "ratio" in rec
    # log-always semantics: near-miss fields present, ratio may be 0


def test_normal_query_no_mining(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("logs", exist_ok=True)
    build_plan(question="昨天GMV是多少", keywords=[], today=datetime.date(2026, 8, 7))
    f = tmp_path / "logs" / "alias_candidates.jsonl"
    assert not f.exists()
