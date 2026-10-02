"""M10 spill store tests (pure logic, no DB for thresholds)."""
import asyncio

from app.services.spill_store import (
    should_spill, head_tail, _json_size,
)


def _rows(n):
    return [{"id": i, "val": "x" * 10} for i in range(n)]


def test_should_spill_by_rows():
    assert should_spill(_rows(501)) is True
    assert should_spill(_rows(10)) is False


def test_should_spill_by_bytes(monkeypatch):
    monkeypatch.setattr("app.services.spill_store.SPILL_MIN_ROWS", 100000)
    monkeypatch.setattr("app.services.spill_store.SPILL_MIN_BYTES", 100)
    assert should_spill(_rows(50)) is True   # 50*~20 bytes > 100


def test_json_size_positive():
    assert _json_size([{"a": 1}]) > 0


def test_head_tail_small_result_unchanged():
    rows = _rows(30)
    assert head_tail(rows) == rows


def test_head_tail_marks_omission():
    rows = _rows(1000)
    out = head_tail(rows, head=50, tail=10)
    assert len(out) == 61
    assert out[0]["id"] == 0
    assert out[50]["__omitted__"] == 940
    assert out[-1]["id"] == 999


def test_head_tail_full_when_fits():
    rows = _rows(60)
    out = head_tail(rows, head=50, tail=10)
    assert len(out) == 60  # 60 <= 50+10, no omission marker


def test_save_spill_returns_none_below_threshold():
    from app.services.spill_store import save_spill

    class _S:
        async def execute(self, *a, **k):
            raise AssertionError("should not write")
    out = asyncio.run(save_spill(_S(), "s", "SELECT 1", _rows(5)))
    assert out is None


def test_resolve_full_falls_back_without_spill():
    from app.services.spill_store import resolve_full_rows

    class _S:
        pass
    state = {"_last_result": [{"a": 1}]}
    out = asyncio.run(resolve_full_rows(state, _S()))
    assert out == [{"a": 1}]
