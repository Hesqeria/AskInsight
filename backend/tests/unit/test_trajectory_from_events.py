"""M1 FR6: trajectory evaluation from the session event log."""
from app.eval.evaluator.trajectory import evaluate_trajectory_from_events


def _events(*pairs):
    return [{"seq": i, "type": t, "payload": p} for i, (t, p) in enumerate(pairs)]


def test_happy_path_from_events():
    evts = _events(
        ("query/received", {"query": "上月GMV"}),
        ("intent/resolved", {"intent": "query"}),
        ("recall/merged", {"tables": 3, "metrics": 2}),
        ("sql/generated", {"sql": "SELECT 1"}),
        ("sql/validated", {"ok": True}),
        ("guard/decision", {"guard": "forbidden_keywords", "outcome": "pass"}),
        ("sql/executed", {"rows": 10}),
        ("state/checkpoint", {}),
        ("turn/ended", {"reason": "success"}),
    )
    rep = evaluate_trajectory_from_events(
        question="上月GMV", gold_sql="SELECT 1", events=evts)
    d = rep.to_dict()
    assert d["passed"] is True
    assert "intent_routed" in [a["name"] for a in d["assertions"]]
    assert d["assertions"][-1]["detail"].startswith("9 events")


def test_correction_loop_detected():
    evts = _events(
        ("sql/validated", {"ok": False, "error": "x"}),
        ("sql/validated", {"ok": False, "error": "x"}),
        ("sql/validated", {"ok": False, "error": "x"}),
        ("sql/validated", {"ok": False, "error": "x"}),
    )
    rep = evaluate_trajectory_from_events(
        question="q", gold_sql="SELECT 1", events=evts)
    assert not rep.passed
    assert "no_correction_loop" in rep.failed_names


def test_empty_events_fail_completeness():
    rep = evaluate_trajectory_from_events(
        question="q", gold_sql="SELECT 1", events=[])
    names = [a.name for a in rep.assertions]
    assert "event_log_complete" in names
    assert not rep.passed
