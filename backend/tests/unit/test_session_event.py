"""M1 session event logger tests: freeze/truncate, queue/drain, seq
monotonicity, fallback file."""
import asyncio
import json

from app.core.session_event import (
    SessionEventLogger, _freeze, FALLBACK_FILE,
)


def test_freeze_deep_copies():
    payload = {"a": {"b": [1, 2]}}
    frozen = _freeze(payload)
    assert frozen == payload
    frozen["a"]["b"].append(3)
    assert payload["a"]["b"] == [1, 2]  # original untouched


def test_freeze_truncates_long_strings():
    s = "x" * 10000
    out = _freeze({"sql": s})
    assert len(out["sql"]) < 6200
    assert "truncated" in out["sql"]


def test_submit_then_flush_drains_queue(monkeypatch, tmp_path):
    logger = SessionEventLogger()
    written = []

    async def fake_write(pending):
        written.extend(pending)

    monkeypatch.setattr(logger, "_write", fake_write)
    logger.submit("s1", "query/received", {"q": "hi"})
    logger.submit("s1", "turn/ended", {"reason": "success"})
    logger.submit("s2", "query/received", {"q": "other"})
    assert logger.pending_count() == 3
    asyncio.run(logger.flush("s1"))
    assert logger.pending_count() == 1  # s2 kept
    assert [e.type for e in written] == ["query/received", "turn/ended"]
    assert all(e.session_id == "s1" for e in written)


def test_write_falls_back_to_file(monkeypatch, tmp_path):
    logger = SessionEventLogger()
    from app.repositories.doris.session_event import session_event_repository as repo_mod

    async def boom_factory():
        raise RuntimeError("db down")

    # Force the Doris session factory to explode.
    import app.clients.doris_client_manager as dcm
    monkeypatch.setattr(dcm.doris_client_manager, "session_factory", boom_factory)
    monkeypatch.setattr("app.core.session_event.FALLBACK_FILE", tmp_path / "fb.log")

    from app.repositories.doris.session_event.session_event_repository import SessionEvent
    events = [SessionEvent("sx", 1, "query/received", {"q": "x"})]
    logger._write_file_fallback(events)
    content = (tmp_path / "fb.log").read_text(encoding="utf-8")
    assert "query/received" in content
    obj = json.loads(content.strip())
    assert obj["session_id"] == "sx"


def test_seq_counter_monotonic(monkeypatch):
    logger = SessionEventLogger()
    # No DB: max_seq path guarded by exception -> counters start at 0.
    import app.clients.doris_client_manager as dcm

    class _FakeRepo:
        def __init__(self, session):
            pass

        async def max_seq(self, sid):
            return 5

        async def insert_batch(self, events):
            _FakeRepo.captured = events
            return True

    class _FakeSessionCtx:
        def __init__(self):
            self.repo = None

        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    # Patch the repository class used inside _write.
    import app.core.session_event as se_mod
    from app.repositories.doris.session_event import session_event_repository as sr
    monkeypatch.setattr(sr, "SessionEventRepository", _FakeRepo)
    monkeypatch.setattr(dcm.doris_client_manager, "session_factory", _FakeSessionCtx)

    _FakeRepo.captured = getattr(_FakeRepo, "captured", None)
    logger.submit("s1", "a", {})
    logger.submit("s1", "b", {})
    asyncio.run(logger.flush("s1"))
    # Counters resumed from MAX(seq)=5 -> seq 6,7
    assert _FakeRepo.captured is not None
    assert [e.seq for e in _FakeRepo.captured] == [6, 7]
    assert [e.session_id for e in _FakeRepo.captured] == ["s1", "s1"]
