"""Tests for app.eval.dataset.paraphrase (FR-METHOD-09)."""
import random

import pytest

from app.eval.dataset.paraphrase import (
    RuleParaphraser,
    LLMParaphraser,
    ParaphraseReport,
    ParaphraseSummary,
    evaluate_paraphrase_stability,
    pairwise_ast_similarity,
    summarize_paraphrase,
)


# --------------------------------------------------------------------------- #
# RuleParaphraser
# --------------------------------------------------------------------------- #
class TestRuleParaphraser:
    def test_generates_n_paraphrases(self):
        p = RuleParaphraser()
        out = p.paraphrase("昨天的销售金额是多少", n=3)
        assert len(out) == 3
        assert all(isinstance(x, str) and x for x in out)

    def test_no_duplicate_with_original(self):
        p = RuleParaphraser()
        original = "查一下今天的销售"
        out = p.paraphrase(original, n=5)
        assert original not in out

    def test_no_duplicates_within_output(self):
        p = RuleParaphraser()
        out = p.paraphrase("昨天的销售金额是多少", n=5)
        assert len(out) == len(set(out))

    def test_synonym_substitution(self):
        # "金额" is a known synonym.
        p = RuleParaphraser()
        out = p.paraphrase("昨天的销售金额是多少", n=5)
        # At least one paraphrase should differ in wording.
        assert any("金额" not in x or "销售" not in x or x != "昨天的销售金额是多少"
                   for x in out)

    def test_deterministic_with_seed(self):
        # Same RNG seed -> same output.
        a = RuleParaphraser(rng=random.Random(42))
        b = RuleParaphraser(rng=random.Random(42))
        qa = a.paraphrase("昨天的销售金额是多少", n=3)
        qb = b.paraphrase("昨天的销售金额是多少", n=3)
        assert qa == qb

    def test_empty_question(self):
        assert RuleParaphraser().paraphrase("", n=3) == []

    def test_zero_n(self):
        assert RuleParaphraser().paraphrase("anything", n=0) == []

    def test_polite_prefix_fallback(self):
        # Unusual input that doesn't match any rule should still get a paraphrase.
        p = RuleParaphraser()
        out = p.paraphrase("zzz unknown phrase zzz", n=2)
        assert len(out) >= 1


# --------------------------------------------------------------------------- #
# LLMParaphraser (mocked - no real LLM call)
# --------------------------------------------------------------------------- #
class _FakeLLM:
    """Mock LLM that returns canned text lines."""
    def __init__(self, response: str):
        self.response = response
        self.calls = 0

    async def invoke(self, prompt: str) -> str:
        self.calls += 1
        return self.response


class TestLLMParaphraser:
    def test_parses_lines(self):
        fake = _FakeLLM("请问昨天GMV\n昨日成交总额\n昨天卖了多少")
        p = LLMParaphraser(llm_client=fake, n=3)
        import asyncio
        out = asyncio.run(p.paraphrase("昨天GMV多少"))
        assert len(out) == 3
        assert fake.calls == 1

    def test_strips_numbering(self):
        fake = _FakeLLM("1. 请问昨天GMV\n2. 昨日成交\n3. 昨天卖了多少")
        p = LLMParaphraser(llm_client=fake, n=3)
        import asyncio
        out = asyncio.run(p.paraphrase("昨天GMV多少"))
        assert all(not x[0].isdigit() for x in out)

    def test_filters_out_original(self):
        original = "昨天GMV多少"
        fake = _FakeLLM(f"{original}\n另一种问法\n第三种问法")
        p = LLMParaphraser(llm_client=fake, n=3)
        import asyncio
        out = asyncio.run(p.paraphrase(original))
        assert original not in out

    def test_no_client_raises(self):
        p = LLMParaphraser(llm_client=None)
        with pytest.raises(RuntimeError):
            import asyncio
            asyncio.run(p.paraphrase("Q"))

    def test_llm_failure_returns_empty(self):
        class _BoomLLM:
            async def invoke(self, prompt):
                raise RuntimeError("api down")
        p = LLMParaphraser(llm_client=_BoomLLM(), n=3)
        import asyncio
        out = asyncio.run(p.paraphrase("Q"))
        assert out == []


# --------------------------------------------------------------------------- #
# Stability scoring
# --------------------------------------------------------------------------- #
class TestPairwiseAstSimilarity:
    def test_identical_sqls(self):
        s = pairwise_ast_similarity([
            "SELECT a FROM t",
            "SELECT a FROM t",
            "SELECT a FROM t",
        ])
        assert s == pytest.approx(1.0)

    def test_single_sql_is_one(self):
        assert pairwise_ast_similarity(["SELECT a FROM t"]) == 1.0

    def test_empty_is_one(self):
        assert pairwise_ast_similarity([]) == 1.0

    def test_different_sqls_lower_score(self):
        identical = pairwise_ast_similarity(["SELECT a FROM t", "SELECT a FROM t"])
        different = pairwise_ast_similarity([
            "SELECT a FROM t",
            "DROP TABLE users",
        ])
        assert different < identical


# --------------------------------------------------------------------------- #
# Report evaluation
# --------------------------------------------------------------------------- #
class TestEvaluateParaphraseStability:
    def test_stable_predictions_pass(self):
        rep = evaluate_paraphrase_stability(
            question="昨天GMV多少",
            variants=["昨日成交额", "昨天卖了多少", "昨日总成交"],
            pred_sqls_per_variant=[
                "SELECT SUM(amount) FROM orders WHERE dt='2026-08-10'",
                "SELECT SUM(amount) FROM orders WHERE dt='2026-08-10'",
                "SELECT SUM(amount) FROM orders WHERE dt='2026-08-10'",
            ],
            stability_threshold=0.85,
        )
        assert rep.stability == pytest.approx(1.0)
        assert rep.all_passed_threshold is True

    def test_unstable_predictions_fail(self):
        rep = evaluate_paraphrase_stability(
            question="Q",
            variants=["V1", "V2"],
            pred_sqls_per_variant=[
                "SELECT a FROM t1",
                "SELECT x FROM totally_different_table WHERE y > 100",
            ],
            stability_threshold=0.85,
        )
        assert rep.stability < 0.85
        assert rep.all_passed_threshold is False

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            evaluate_paraphrase_stability(
                question="Q",
                variants=["V1", "V2"],
                pred_sqls_per_variant=["SQL1"],  # length mismatch
            )

    def test_to_dict_serializable(self):
        rep = evaluate_paraphrase_stability(
            question="Q", variants=["V1"],
            pred_sqls_per_variant=["SELECT 1"],
        )
        d = rep.to_dict()
        for k in ("question", "variants", "pred_sqls", "stability", "passed"):
            assert k in d


# --------------------------------------------------------------------------- #
# Summary aggregation
# --------------------------------------------------------------------------- #
class TestSummarizeParaphrase:
    def test_empty(self):
        s = summarize_paraphrase([])
        assert s.total == 0
        assert s.worst == []

    def test_aggregates_avg_stability(self):
        reps = [
            ParaphraseReport(question="Q1", stability=1.0, all_passed_threshold=True),
            ParaphraseReport(question="Q2", stability=0.5, all_passed_threshold=False),
        ]
        s = summarize_paraphrase(reps)
        assert s.total == 2
        assert s.passed == 1
        assert s.avg_stability == pytest.approx(0.75)

    def test_worst_n_returned(self):
        reps = [
            ParaphraseReport(question=f"Q{i}", stability=0.1 * i)  # 0.1, 0.2, ..., 1.0
            for i in range(1, 11)
        ]
        s = summarize_paraphrase(reps, bottom_n=5)
        assert len(s.worst) == 5
        # Worst should be sorted ascending (lowest first).
        assert s.worst[0]["stability"] == pytest.approx(0.1)
        assert s.worst[4]["stability"] == pytest.approx(0.5)
