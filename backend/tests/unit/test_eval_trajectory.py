"""Tests for app.eval.evaluator.trajectory (FR-METHOD-12)."""
import pytest

from app.eval.evaluator.trajectory import (
    StepAssertion,
    TrajectoryReport,
    TrajectorySummary,
    assert_intent_routed,
    assert_no_correction_loop,
    assert_recall_quality,
    assert_step_count,
    assert_table_recall,
    evaluate_trajectory,
    summarize_trajectories,
)


# --------------------------------------------------------------------------- #
# Step assertions
# --------------------------------------------------------------------------- #
class TestAssertRecallQuality:
    def test_perfect_recall(self):
        a = assert_recall_quality(
            recalled_columns=["amount", "uid", "order_id"],
            gold_sql="SELECT amount, uid FROM orders WHERE order_id > 0",
        )
        assert a.passed is True
        assert a.score == pytest.approx(1.0)

    def test_missing_columns_fail(self):
        # Gold needs 3 cols, recalled 1 -> recall=1/3 < 0.8 -> fail
        a = assert_recall_quality(
            recalled_columns=["amount"],
            gold_sql="SELECT amount, uid, region FROM orders",
        )
        assert a.passed is False
        assert "missing" in a.detail

    def test_no_recalled_columns(self):
        a = assert_recall_quality(
            recalled_columns=[],
            gold_sql="SELECT amount FROM orders",
        )
        assert a.passed is False

    def test_no_columns_in_gold(self):
        a = assert_recall_quality(
            recalled_columns=[],
            gold_sql="SELECT 1",
        )
        assert a.passed is True


class TestAssertTableRecall:
    def test_all_tables_caught(self):
        a = assert_table_recall(
            recalled_tables=["orders", "users"],
            gold_sql="SELECT * FROM orders o JOIN users u ON o.uid=u.id",
        )
        assert a.passed is True
        assert a.score == 1.0

    def test_missing_table(self):
        a = assert_table_recall(
            recalled_tables=["orders"],
            gold_sql="SELECT * FROM orders o JOIN users u ON o.uid=u.id",
        )
        assert a.passed is False
        assert a.score < 1.0


class TestAssertNoCorrectionLoop:
    def test_within_limit(self):
        assert assert_no_correction_loop(0).passed is True
        assert assert_no_correction_loop(1).passed is True
        assert assert_no_correction_loop(3).passed is True

    def test_exceeds_limit(self):
        assert assert_no_correction_loop(4).passed is False
        assert assert_no_correction_loop(10).passed is False

    def test_custom_limit(self):
        assert assert_no_correction_loop(5, max_corrections=5).passed is True
        assert assert_no_correction_loop(6, max_corrections=5).passed is False


class TestAssertStepCount:
    def test_optimal_steps(self):
        a = assert_step_count(5, soft_max=8, hard_max=15)
        assert a.passed is True
        assert a.score == 1.0

    def test_too_many_steps(self):
        a = assert_step_count(20, soft_max=8, hard_max=15)
        assert a.passed is False
        assert a.score == 0.0

    def test_zero_steps(self):
        a = assert_step_count(0)
        assert a.passed is False

    def test_partial_score_between_soft_and_hard(self):
        a = assert_step_count(11, soft_max=8, hard_max=15)
        assert a.passed is True  # still under hard_max
        assert 0.0 < a.score < 1.0


class TestAssertIntentRouted:
    def test_correct_intent(self):
        a = assert_intent_routed("query", expected="query")
        assert a.passed is True

    def test_wrong_intent(self):
        a = assert_intent_routed("chat", expected="query")
        assert a.passed is False

    def test_case_insensitive(self):
        assert assert_intent_routed("Query").passed is True
        assert assert_intent_routed("QUERY").passed is True


# --------------------------------------------------------------------------- #
# TrajectoryReport aggregation
# --------------------------------------------------------------------------- #
class TestTrajectoryReport:
    def test_all_pass(self):
        rep = TrajectoryReport(
            question="Q",
            assertions=[
                StepAssertion("a", True, 1.0),
                StepAssertion("b", True, 0.9),
            ],
        )
        assert rep.passed is True
        assert rep.avg_score == pytest.approx(0.95)
        assert rep.failed_names == []

    def test_some_fail(self):
        rep = TrajectoryReport(
            question="Q",
            assertions=[
                StepAssertion("a", True, 1.0),
                StepAssertion("b", False, 0.0, "broken"),
            ],
        )
        assert rep.passed is False
        assert rep.failed_names == ["b"]


class TestEvaluateTrajectory:
    def test_full_battery(self):
        rep = evaluate_trajectory(
            question="昨天GMV多少",
            gold_sql="SELECT SUM(amount) FROM orders WHERE dt='2026-08-10'",
            intent="query",
            recalled_columns=["amount", "dt"],
            recalled_tables=["orders"],
            correction_count=1,
            steps=5,
        )
        assert isinstance(rep, TrajectoryReport)
        assert len(rep.assertions) == 5  # all five assertions fired
        assert rep.passed is True

    def test_partial_battery(self):
        # Provide realistic multi-char column name so recall_quality fires.
        rep = evaluate_trajectory(
            question="Q",
            gold_sql="SELECT amount FROM orders",
            intent="query",
            recalled_columns=["amount"],  # explicit so recall_quality runs
        )
        # intent + recall_quality + no_correction_loop (count=0 default)
        assert len(rep.assertions) == 3
        assert rep.assertions[0].name == "intent_routed"

    def test_failing_trajectory(self):
        rep = evaluate_trajectory(
            question="Q",
            gold_sql="SELECT a_amount, b_uid, c_region, d_date, e_status FROM orders",
            intent="chat",  # wrong
            recalled_columns=["a_amount"],  # recall too low (5 needed)
            correction_count=5,  # over limit
            steps=20,  # too many
        )
        assert rep.passed is False
        assert "intent_routed" in rep.failed_names
        assert "recall_quality" in rep.failed_names
        assert "no_correction_loop" in rep.failed_names


# --------------------------------------------------------------------------- #
# Multi-trajectory summary
# --------------------------------------------------------------------------- #
class TestSummarizeTrajectories:
    def test_empty(self):
        s = summarize_trajectories([])
        assert s.total == 0
        assert s.passed == 0

    def test_failure_rate_aggregation(self):
        reports = [
            TrajectoryReport(assertions=[
                StepAssertion("recall_quality", True),
                StepAssertion("intent_routed", True),
            ]),
            TrajectoryReport(assertions=[
                StepAssertion("recall_quality", False),  # 1 fail
                StepAssertion("intent_routed", True),
            ]),
            TrajectoryReport(assertions=[
                StepAssertion("recall_quality", False),  # 2 fail
                StepAssertion("intent_routed", False),   # 1 fail
            ]),
        ]
        s = summarize_trajectories(reports)
        assert s.total == 3
        assert s.passed == 1
        # recall_quality: 2/3 failed
        assert s.failure_rate_by_assertion["recall_quality"] == pytest.approx(2/3, abs=0.01)
        # intent_routed: 1/3 failed
        assert s.failure_rate_by_assertion["intent_routed"] == pytest.approx(1/3, abs=0.01)
