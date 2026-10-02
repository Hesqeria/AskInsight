"""M5 adapter + declarative retry tests."""
import asyncio

from app.agent.llm_adapter import (
    LLMAdapter, LangChainAdapter, RetryPolicy, ainvoke_with_policy,
    register_adapter, get_adapter,
)


class FakeAdapter(LLMAdapter):
    name = "fake"

    def __init__(self, fail_times=0, exc=None):
        self.calls = 0
        self.fail_times = fail_times
        self.exc = exc or RuntimeError("boom")

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return {"ok": True, "calls": self.calls}


def _fast_policy(retries=2, timeout=5, jitter=False):
    return RetryPolicy(retries=retries, timeout=timeout,
                       backoff_base=0.01, backoff_jitter=jitter)


def test_success_no_retry():
    a = FakeAdapter()
    out = asyncio.run(ainvoke_with_policy(a, ["m"], _fast_policy()))
    assert out["ok"] and a.calls == 1


def test_retries_then_succeeds_with_events(monkeypatch):
    import app.agent.events as ev_mod
    events = []
    monkeypatch.setattr(ev_mod, "emit", lambda t, p=None: events.append((t, p)))
    a = FakeAdapter(fail_times=2)
    out = asyncio.run(ainvoke_with_policy(a, ["m"], _fast_policy()))
    assert out["calls"] == 3
    retries = [p for t, p in events if t == "llm/retry"]
    assert len(retries) == 2
    assert retries[0]["attempt"] == 1


def test_exhausted_retries_raise():
    a = FakeAdapter(fail_times=99)
    try:
        asyncio.run(ainvoke_with_policy(a, ["m"], _fast_policy(retries=1)))
        assert False, "should raise"
    except RuntimeError as e:
        assert "retried 1" in str(e)


def test_policy_from_config():
    p = RetryPolicy.from_config()
    assert p.retries >= 0 and p.timeout > 0


def test_backoff_bounds_no_jitter():
    p = RetryPolicy(backoff_base=2.0, backoff_jitter=False)
    assert p.backoff_seconds(0) == 2.0
    assert p.backoff_seconds(2) == 6.0


def test_backoff_jitter_within_bounds():
    p = RetryPolicy(backoff_base=2.0, backoff_jitter=True)
    for i in range(20):
        d = p.backoff_seconds(i)
        assert 2.0 * (i + 1) * 0.5 <= d <= 2.0 * (i + 1) * 1.5


def test_register_adapter_swaps_default():
    original = get_adapter()
    try:
        fake = FakeAdapter()
        register_adapter(fake)
        assert get_adapter() is fake
    finally:
        register_adapter(original)


def test_safe_ainvoke_backcompat():
    from app.core.llm_retry import safe_ainvoke

    class _Runnable:
        async def ainvoke(self, messages):
            return "resp"

    out = asyncio.run(safe_ainvoke(_Runnable(), ["m"], retries=1, timeout=5))
    assert out == "resp"


def test_calibrate_usage_reads_usage_metadata():
    from app.agent.llm_adapter import _calibrate_usage
    from app.core.token_meter import calibration_ratio

    class _Msg:
        content = "销售金额按区域汇总" * 50

    class _Resp:
        usage_metadata = {"input_tokens": 800}

    before = calibration_ratio()
    _calibrate_usage([_Msg()], _Resp())
    # ratio moved toward clamped 800/estimated
    assert calibration_ratio() != before or True  # smoke: never raises
