"""Fast-lane gate: simple lookups skip the analysis tail."""
from app.agent.nodes._analysis_gate import analysis_wanted


def test_single_value_no_analysis():
    state = {"query": "昨天GMV是多少", "_last_result": [{"v": 123}]}
    assert analysis_wanted(state) is False


def test_multi_row_no_longer_auto_triggers():
    # Auto-insight by row count was removed (~25s per multi-row answer);
    # plain lookups stay on the fast lane, intent words still trigger.
    state = {"query": "各区域GMV", "_last_result": [{"r": i} for i in range(5)]}
    assert analysis_wanted(state) is False
    import os
    os.environ["ANALYSIS_MIN_ROWS"] = "3"
    try:
        import importlib
        import app.agent.nodes._analysis_gate as gate
        importlib.reload(gate)
        assert gate.analysis_wanted(state) is True
    finally:
        os.environ.pop("ANALYSIS_MIN_ROWS", None)
        importlib.reload(gate)


def test_analysis_intent_overrides():
    state = {"query": "为什么昨天GMV下降了", "_last_result": [{"v": 1}]}
    assert analysis_wanted(state) is True


def test_empty_result_no_analysis():
    state = {"query": "昨天GMV", "_last_result": []}
    assert analysis_wanted(state) is False
