"""M4 compaction tests: prune-first, summary fallback, event pair."""
import asyncio

from app.services.compaction import (
    maybe_compact, _prune_old, PRUNE_KEEP_CHARS,
)


def test_prune_keeps_recent_intact():
    history = [f"问题{i}" + "x" * 200 for i in range(6)]
    pruned, count = _prune_old(history, keep_recent=2)
    assert count == 4
    assert pruned[-1] == history[-1]      # recent untouched
    assert pruned[-2] == history[-2]
    assert all(len(p) <= PRUNE_KEEP_CHARS for p in pruned[:-2])


def test_short_history_untouched():
    history = ["问题1", "问题2"]
    pruned, count = _prune_old(history)
    assert count == 0
    assert pruned == history


def test_maybe_compact_noop_under_threshold():
    out = asyncio.run(maybe_compact(["短问题"], budget=10_000))
    assert out.pruned == 0 and out.summarized == 0
    assert out.history == ["短问题"]


def test_maybe_compact_prunes_over_threshold(monkeypatch):
    import app.services.compaction as c
    events = []
    import app.agent.events as ev_mod
    monkeypatch.setattr(ev_mod, "emit", lambda t, p=None: events.append(t))
    # LLM summarizer unavailable -> prune-only path (crash-tolerant).
    async def boom(prompt):
        raise RuntimeError("llm down")
    monkeypatch.setattr(c, "_llm_summarize", boom)
    history = [f"第{i}个数据问题" + "y" * 300 for i in range(8)]
    out = asyncio.run(maybe_compact(history, budget=200))
    assert out.pruned > 0
    assert out.used_llm is False
    assert len(out.history) == len(history)
    assert "compaction/start" in events
    assert "compaction/end" in events      # transaction closes even on LLM failure


def test_maybe_compact_summarizes_when_still_over(monkeypatch):
    import app.services.compaction as c
    events = []
    import app.agent.events as ev_mod
    monkeypatch.setattr(ev_mod, "emit", lambda t, p=None: events.append(t))

    async def fake_sum(entries):
        return "[历史摘要] 用户查询销售与库存指标"
    monkeypatch.setattr(c, "_llm_summarize", fake_sum)
    history = [f"第{i}个数据问题" + "y" * 300 for i in range(8)]
    out = asyncio.run(maybe_compact(history, budget=150))
    assert out.used_llm is True
    assert out.history[0].startswith("[历史摘要]")
    assert "compaction/summary" in events
    assert "compaction/start" in events and "compaction/end" in events
