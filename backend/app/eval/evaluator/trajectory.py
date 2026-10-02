"""Trajectory Evaluation (NL2SQL其他测评方法-PRD.md FR-METHOD-12).

Agent paths go through multiple nodes:
    intent → recall → merge → generate → validate → correct → execute → ...

Three layers of evaluation:
  - Step-level:   each node output sane? (recall quality, no hallucinated cols)
  - Path-level:   steps count, did we hit the right branch, any loops?
  - Task-level:   final task succeeded? (already covered by EX/LLM-Judge)

This module provides reusable assertion primitives + a TrajectoryReport
aggregator. Plug into run_eval by capturing `state` from
`graph.astream` chunks per node.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..utils import extract_referenced_columns, extract_referenced_tables


# --------------------------------------------------------------------------- #
# Per-step assertions
# --------------------------------------------------------------------------- #
@dataclass
class StepAssertion:
    """One assertion result on a single trajectory step."""
    name: str
    passed: bool
    score: float = 0.0           # 0..1 fractional score if partial
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "score": round(self.score, 4),
            "detail": self.detail,
        }


def assert_recall_quality(
    recalled_columns: Iterable[str],
    gold_sql: str,
) -> StepAssertion:
    """FR-METHOD-12 step assertion: recall node must surface all columns
    the gold SQL references. Returns precision+recall F1.

    Args:
        recalled_columns: column IDs/names that the RAG layer surfaced.
        gold_sql: the gold SQL for the question.
    """
    recalled = {str(c).lower() for c in recalled_columns if c}
    gold_cols = extract_referenced_columns(gold_sql)

    if not gold_cols:
        return StepAssertion("recall_quality", True, 1.0,
                             "gold SQL has no columns to verify against")
    if not recalled:
        return StepAssertion("recall_quality", False, 0.0,
                             f"no columns recalled; gold needs {sorted(gold_cols)}")

    tp = len(gold_cols & recalled)
    precision = tp / len(recalled)
    recall = tp / len(gold_cols)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    missing = gold_cols - recalled
    extra = recalled - gold_cols
    detail_parts = [f"recall={recall:.2f}", f"precision={precision:.2f}"]
    if missing:
        detail_parts.append(f"missing={sorted(missing)[:5]}")
    if extra:
        detail_parts.append(f"extra={sorted(extra)[:5]}")
    return StepAssertion(
        "recall_quality",
        passed=(recall >= 0.8),
        score=f1,
        detail=" | ".join(detail_parts),
    )


def assert_table_recall(
    recalled_tables: Iterable[str],
    gold_sql: str,
) -> StepAssertion:
    """Same as assert_recall_quality but at the table level (coarser).

    Use this when the column-level signal is too noisy (e.g. ad-hoc
    aliasing) or when debugging the table router specifically.
    """
    recalled = {str(t).lower() for t in recalled_tables if t}
    gold_tables = extract_referenced_tables(gold_sql)
    if not gold_tables:
        return StepAssertion("table_recall", True, 1.0, "no tables in gold")
    if not recalled:
        return StepAssertion("table_recall", False, 0.0, "no tables recalled")
    tp = len(gold_tables & recalled)
    recall = tp / len(gold_tables)
    missing = gold_tables - recalled
    return StepAssertion(
        "table_recall",
        passed=(recall == 1.0),
        score=recall,
        detail=f"missing={sorted(missing)}" if missing else "all caught",
    )


def assert_no_correction_loop(
    correction_count: int,
    max_corrections: int = 3,
) -> StepAssertion:
    """FR-METHOD-12 path assertion: validate→correct should not loop more
    than `max_corrections` times. Excessive loops = stuck Agent.
    """
    passed = correction_count <= max_corrections
    return StepAssertion(
        "no_correction_loop",
        passed=passed,
        score=1.0 if passed else 0.0,
        detail=f"corrections={correction_count} (max={max_corrections})",
    )


def assert_step_count(
    steps: int,
    soft_max: int = 8,
    hard_max: int = 15,
) -> StepAssertion:
    """Path assertion: trajectory should be neither too short (skipped nodes)
    nor too long (wandering)."""
    if steps == 0:
        return StepAssertion("step_count", False, 0.0, "empty trajectory")
    if steps <= hard_max:
        score = 1.0 if steps <= soft_max else 1.0 - (steps - soft_max) / (hard_max - soft_max)
        score = max(0.0, min(1.0, score))
        return StepAssertion(
            "step_count", True, score,
            f"steps={steps} (soft_max={soft_max}, hard_max={hard_max})",
        )
    return StepAssertion(
        "step_count", False, 0.0,
        f"steps={steps} exceeds hard_max={hard_max}",
    )


def assert_intent_routed(intent: str, expected: str = "query") -> StepAssertion:
    """Step assertion: intent node correctly classified as 'query' (or
    whatever was expected). Catches Agent answering chat questions with
    SQL or vice versa."""
    passed = (intent or "").lower() == expected.lower()
    return StepAssertion(
        "intent_routed", passed, 1.0 if passed else 0.0,
        f"intent={intent!r} expected={expected!r}",
    )


# --------------------------------------------------------------------------- #
# Trajectory report
# --------------------------------------------------------------------------- #
@dataclass
class TrajectoryReport:
    """Aggregate of step + path assertions for one trajectory."""
    question: str = ""
    assertions: list[StepAssertion] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(a.passed for a in self.assertions)

    @property
    def avg_score(self) -> float:
        if not self.assertions:
            return 0.0
        return sum(a.score for a in self.assertions) / len(self.assertions)

    @property
    def failed_names(self) -> list[str]:
        return [a.name for a in self.assertions if not a.passed]

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "passed": self.passed,
            "avg_score": round(self.avg_score, 4),
            "failed": self.failed_names,
            "assertions": [a.to_dict() for a in self.assertions],
        }


def evaluate_trajectory(
    *,
    question: str,
    gold_sql: str,
    intent: str = "query",
    recalled_columns: Iterable[str] = (),
    recalled_tables: Iterable[str] = (),
    correction_count: int = 0,
    steps: int = 0,
    max_corrections: int = 3,
    soft_step_max: int = 8,
    hard_step_max: int = 15,
) -> TrajectoryReport:
    """Run the standard assertion battery against one trajectory.

    All inputs are optional — pass only what you have. Missing inputs
    will skip the corresponding assertion (so partial instrumentation
    still works).
    """
    rep = TrajectoryReport(question=question)
    if intent:
        rep.assertions.append(assert_intent_routed(intent))
    if recalled_columns or recalled_tables:
        if recalled_columns:
            rep.assertions.append(assert_recall_quality(recalled_columns, gold_sql))
        if recalled_tables:
            rep.assertions.append(assert_table_recall(recalled_tables, gold_sql))
    if correction_count is not None:
        rep.assertions.append(assert_no_correction_loop(correction_count, max_corrections))
    if steps:
        rep.assertions.append(assert_step_count(steps, soft_step_max, hard_step_max))
    return rep


# --------------------------------------------------------------------------- #
# Multi-trajectory aggregation (a full eval run)
# --------------------------------------------------------------------------- #
@dataclass
class TrajectorySummary:
    total: int = 0
    passed: int = 0
    avg_score: float = 0.0
    failure_rate_by_assertion: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "pass_rate": round(self.passed / self.total, 4) if self.total else 0.0,
            "avg_score": round(self.avg_score, 4),
            "failure_rate_by_assertion": {
                k: round(v, 4) for k, v in self.failure_rate_by_assertion.items()
            },
        }


def summarize_trajectories(reports: list[TrajectoryReport]) -> TrajectorySummary:
    """Roll per-trajectory reports into a summary.

    The `failure_rate_by_assertion` dict shows which step types fail
    most often across the run — useful for prioritizing prompt fixes.
    """
    s = TrajectorySummary(total=len(reports))
    if not reports:
        return s
    s.passed = sum(1 for r in reports if r.passed)
    s.avg_score = sum(r.avg_score for r in reports) / len(reports)

    by_name_total: dict[str, int] = {}
    by_name_fail: dict[str, int] = {}
    for r in reports:
        for a in r.assertions:
            by_name_total[a.name] = by_name_total.get(a.name, 0) + 1
            if not a.passed:
                by_name_fail[a.name] = by_name_fail.get(a.name, 0) + 1
    s.failure_rate_by_assertion = {
        n: by_name_fail.get(n, 0) / by_name_total[n]
        for n in by_name_total
    }
    return s


# --------------------------------------------------------------------------- #
# M1 FR6: build trajectories directly from the session event log.
# The event timeline replaces manual per-node instrumentation as the
# assertion input source ("replaying the log IS the state").
# --------------------------------------------------------------------------- #
def evaluate_trajectory_from_events(
    *,
    question: str,
    gold_sql: str,
    events: list[dict],
    soft_step_max: int = 8,
    hard_step_max: int = 15,
    max_corrections: int = 3,
) -> TrajectoryReport:
    """Evaluate one trajectory from a session_event stream.

    `events` is a list of {seq, type, payload} dicts (the shape returned
    by SessionEventRepository.list_events / GET /sessions/{id}/events).
    """
    by_type: dict[str, dict] = {}
    guard_decisions = 0
    rewrites = 0
    for ev in events or []:
        etype = ev.get("type", "")
        payload = ev.get("payload") or {}
        if etype == "recall/merged":
            by_type.setdefault("recall", payload)
        elif etype == "intent/resolved":
            by_type["intent"] = payload.get("intent", "query")
        elif etype == "guard/decision":
            guard_decisions += 1
            if payload.get("outcome") == "rewrite":
                rewrites += 1
        elif etype == "sql/generated":
            by_type.setdefault("sql", payload)

    recall = by_type.get("recall") or {}
    # recall/merged carries counts only; column-level recall still needs
    # the caller to pass recalled_columns explicitly (state detail is
    # slimmed in the checkpoint by design).
    rep = TrajectoryReport(question=question)
    if by_type.get("intent"):
        rep.assertions.append(assert_intent_routed(str(by_type["intent"])))
    # Count sql/validated failures (error->correct_sql loops).
    validation_failures = sum(
        1 for ev in events or []
        if ev.get("type") == "sql/validated"
        and not (ev.get("payload") or {}).get("ok", True)
    )
    rep.assertions.append(
        assert_no_correction_loop(validation_failures, max_corrections))
    stage_count = len({ev.get("type") for ev in events or []})
    rep.assertions.append(assert_step_count(stage_count, soft_step_max,
                                            hard_step_max))
    rep.assertions.append(StepAssertion(
        "event_log_complete", passed=bool(events),
        score=1.0 if events else 0.0,
        detail=f"{len(events or [])} events, {guard_decisions} guard "
               f"decisions, {rewrites} rewrites",
    ))
    return rep
