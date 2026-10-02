"""M8 token meter + budget tests."""
from app.core.token_meter import (
    estimate_tokens, calibrate, calibration_ratio,
    BudgetConfig, budget_parts,
)


def test_estimate_cjk_vs_ascii():
    cjk = estimate_tokens("销售金额按区域")  # 7 CJK chars ~ 7 tokens
    ascii_t = estimate_tokens("abcdefgh")   # 8 ascii ~ 2 tokens
    assert 5 <= cjk <= 9
    assert 1 <= ascii_t <= 3


def test_estimate_empty():
    assert estimate_tokens("") == 0


def test_calibrate_clamped_and_smoothed():
    import app.core.token_meter as _tm
    _tm._calibration_ratio = 1.0   # isolate from other tests' mutations
    before = calibration_ratio()
    calibrate(100, 1000)   # raw ratio 10 -> clamped to 2.0
    mid = calibration_ratio()
    # smoothing: 0.8*before + 0.2*2.0
    assert abs(mid - (0.8 * before + 0.4)) < 1e-6
    calibrate(0, 100)     # no-op on non-positive estimated
    assert calibration_ratio() == mid


def test_budget_trims_ddl_over_share():
    cfg = BudgetConfig(total=1000)
    parts = {
        "ddl": "CREATE TABLE " + "x" * 9000,   # way over 60% of 1000
        "metrics": "m",
        "query": "上月GMV",
    }
    bounded, trims = budget_parts(parts, cfg)
    assert any(t.part == "ddl" for t in trims)
    assert len(bounded["ddl"]) < len(parts["ddl"])
    # query is never trimmed
    assert bounded["query"] == parts["query"]


def test_budget_no_trim_when_within():
    cfg = BudgetConfig(total=100000)
    parts = {"ddl": "small", "query": "q"}
    bounded, trims = budget_parts(parts, cfg)
    assert trims == []
    assert bounded == parts


def test_budget_caps_few_shot_and_history():
    cfg = BudgetConfig(total=1000)  # few_shot share = 150 tokens
    parts = {
        "few_shot": "f" * 5000,
        "history": "h" * 5000,
        "query": "q",
    }
    bounded, trims = budget_parts(parts, cfg)
    trimmed_parts = {t.part for t in trims}
    assert "few_shot" in trimmed_parts
    assert "history" in trimmed_parts


def test_record_pressure_emits_event(monkeypatch):
    from app.core import token_meter as tm
    events = []
    import app.agent.events as ev_mod
    monkeypatch.setattr(ev_mod, "emit", lambda t, p=None: events.append(t))
    parts = {"ddl": "d" * 100}
    bounded = dict(parts)
    out = tm.record_pressure("generate_sql", parts, bounded, [])
    assert out["node"] == "generate_sql"
    assert out["total_estimated"] > 0
    assert "context/pressure" in events
