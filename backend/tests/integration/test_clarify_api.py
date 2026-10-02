"""API tests for clarify_router endpoints.

Uses TestClient with a fake session (SQLAlchemy AsyncSession backed by
a synchronous mock repo) so no real DB is needed. The `_StreamRuntime`
resume path requires the full client-manager stack, so we test the
request-parse + merge logic via direct repo overrides.

Covers:
  - GET  /api/v1/clarify          list pending
  - GET  /api/v1/clarify/{id}     get one (404 handling)
  - POST /api/v1/clarify/{id}/resume  selection path (409 on non-pending)
  - POST /api/v1/clarify/{id}/resume  cancel path
  - free_text parsing unit-level
"""
import os

os.environ.setdefault("JWT_SECRET", "test-secret-key-1234567890")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.clarify_router import clarify_router
from app.api.dependencies import get_meta_session
from app.core.auth import verify_token


# --------------------------------------------------------------------------- #
# Fake repository backing
# --------------------------------------------------------------------------- #
class _FakeClarifyRepo:
    """In-memory stand-in for ClarifyRepository to avoid DB access.
    Returns dict snapshots; router treats them as objects needing
    `.to_dict()` — so we return a small wrapper object."""

    class _S:
        def __init__(self, d):
            self.__dict__.update(d)

        def to_dict(self):
            return dict(self.__dict__)

    def __init__(self):
        self.sessions = {}
        self._seq = 0

    def _make_id(self):
        self._seq += 1
        return f"clr_test_{self._seq}"

    def _wrap(self, d):
        # Return the same dict reference so tests can read back status.
        return d

    async def create(self, question="", initial_plan=None, missing_fields=None,
                     suggestions_given=None, username="", rounds=1):
        cid = self._make_id()
        s = {
            "clarify_id": cid, "question": question,
            "initial_plan": initial_plan or {},
            "final_plan": None, "missing_fields": missing_fields or [],
            "suggestions_given": suggestions_given or [],
            "user_response": None, "status": "pending", "rounds": rounds,
            "username": username, "created_at": "2026-08-12T00:00:00",
            "resolved_at": None,
        }
        self.sessions[cid] = s
        return self._S(s)

    async def get(self, clarify_id):
        s = self.sessions.get(clarify_id)
        return self._S(s) if s else None

    async def list_pending(self, limit=50):
        return [self._S(s) for s in self.sessions.values()
                if s["status"] == "pending"][:limit]

    async def update_response(self, clarify_id, user_response=None,
                              final_plan=None, status="confirmed", rounds=None):
        if clarify_id not in self.sessions:
            return None
        s = self.sessions[clarify_id]
        s["user_response"] = user_response or {}
        s["final_plan"] = final_plan or {}
        s["status"] = status
        if rounds is not None:
            s["rounds"] = rounds
        return self._S(s)


_fake_repo = _FakeClarifyRepo()


def _build_app():
    async def fake_verify_token():
        return {"sub": "test-user", "role": "admin"}

    async def fake_meta_session():
        # The router builds ClarifyRepository(session) but we patch the
        # class to return _fake_repo regardless of session arg.
        yield object()

    app = FastAPI()
    app.include_router(clarify_router)
    app.dependency_overrides[verify_token] = fake_verify_token
    app.dependency_overrides[get_meta_session] = fake_meta_session
    return app


from unittest.mock import patch


def _mock_clarify_repo(monkeypatch=None):
    """Redirect the router's ClarifyRepository construction to _fake_repo."""
    import app.api.routers.clarify_router as mod

    def fake_constructor(session):
        return _fake_repo

    # Replace the class used in the router module.
    patcher = patch.object(mod, "ClarifyRepository", fake_constructor)
    patcher.start()
    return patcher


# --------------------------------------------------------------------------- #
# Free-text parser (unit, no LLM)
# --------------------------------------------------------------------------- #
from app.agent.free_text_parser import _sanitize_selections, _extract_json


class TestFreeTextSanitize:
    def test_valid_selections_passed_through(self):
        out = _sanitize_selections({
            "measure": "GMV", "time": "上月", "group_by": ["C050"],
        })
        assert out["measure"] == "GMV"
        assert out["time"] == "上月"
        assert out["group_by"] == ["C050"]

    def test_unknown_measure_dropped(self):
        out = _sanitize_selections({"measure": "bogus", "time": "上月"})
        assert "measure" not in out
        assert out["time"] == "上月"

    def test_unknown_group_by_class_dropped(self):
        out = _sanitize_selections({"group_by": ["C999", "C050"]})
        assert out["group_by"] == ["C050"]

    def test_dimension_filter_validated(self):
        out = _sanitize_selections({
            "dimension": {"class_id": "C050", "value": "华北"},
        })
        assert out["dimension"]["operator"] == "="
        assert out["dimension"]["value"] == "华北"

    def test_malformed_dimension_dropped(self):
        assert _sanitize_selections({"dimension": {"class_id": "C050"}}) == {}

    def test_extract_json_fenced(self):
        raw = '```json\n{"measure": "GMV"}\n```'
        assert _extract_json(raw) == {"measure": "GMV"}

    def test_extract_json_plain(self):
        assert _extract_json('{"measure": "DAU"}') == {"measure": "DAU"}

    def test_extract_json_with_prose(self):
        raw = '好的,我理解为 {"measure": "order_count"} 你看对吗'
        assert _extract_json(raw) == {"measure": "order_count"}

    def test_extract_json_garbage(self):
        assert _extract_json("not json at all") is None


# --------------------------------------------------------------------------- #
# Router endpoints
# --------------------------------------------------------------------------- #
class TestClarifyApi:
    def setup_method(self):
        _fake_repo.sessions.clear()
        _fake_repo._seq = 0
        self.patcher = _mock_clarify_repo()

    def teardown_method(self):
        self.patcher.stop()

    def _client(self):
        return TestClient(_build_app())

    def _create_session(self, question="测试问题"):
        import asyncio
        s = asyncio.run(_fake_repo.create(
            question=question,
            initial_plan={"question": question, "confidence": 0.5},
            missing_fields=[{"field": "time", "reason": "missing"}],
            suggestions_given=[{"field": "time", "options": []}],
        ))
        # Return the raw dict for easier attribute access.
        return s.to_dict()

    def test_list_empty(self):
        c = self._client()
        r = c.get("/api/v1/clarify")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_list_with_sessions(self):
        self._create_session("Q1")
        self._create_session("Q2")
        c = self._client()
        r = c.get("/api/v1/clarify")
        assert r.status_code == 200
        assert r.json()["count"] == 2

    def test_get_existing(self):
        s = self._create_session("看下销售")
        c = self._client()
        r = c.get(f"/api/v1/clarify/{s['clarify_id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["question"] == "看下销售"
        assert body["status"] == "pending"

    def test_get_missing_returns_404(self):
        c = self._client()
        r = c.get("/api/v1/clarify/clr_nonexistent")
        assert r.status_code == 404

    def test_resume_cancel(self):
        s = self._create_session("Q")
        c = self._client()
        r = c.post(f"/api/v1/clarify/{s['clarify_id']}/resume",
                   json={"response_type": "cancel", "confirmed": False})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
        # Session should be marked abandoned.
        updated = _fake_repo.sessions[s["clarify_id"]]["status"]
        assert updated == "abandoned"

    def test_resume_missing_session(self):
        c = self._client()
        r = c.post("/api/v1/clarify/clr_nonexistent/resume",
                   json={"response_type": "selection", "selections": {}})
        assert r.status_code == 404

    def test_resume_non_pending_conflict(self):
        s = self._create_session("Q")
        _fake_repo.sessions[s["clarify_id"]]["status"] = "confirmed"
        c = self._client()
        r = c.post(f"/api/v1/clarify/{s['clarify_id']}/resume",
                   json={"response_type": "selection", "selections": {}})
        assert r.status_code == 409

    def test_resume_confirmed_builds_stream(self):
        """Selection response with confirmed=True should build the
        confirmed status (stream path requires full stack, so we just
        verify repo got the update before StreamingResponse)."""
        s = self._create_session("看下销售")
        c = self._client()
        # Override the merge function in its SOURCE module so the router's
        # `from ... import apply_clarification_to_plan` picks up the mock.
        import app.agent.nodes.merge_clarification as mcm
        with patch.object(
            mcm, "apply_clarification_to_plan",
            return_value={
                "question": "看下销售", "measures": [],
                "confidence": 0.9, "notes": [],
            },
        ):
            r = c.post(
                f"/api/v1/clarify/{s['clarify_id']}/resume",
                json={
                    "response_type": "selection",
                    "selections": {"measure": "GMV", "time": "上月"},
                    "confirmed": True,
                },
            )
            # The update happens BEFORE StreamingResponse is returned, so
            # we can assert session status now.
            assert _fake_repo.sessions[s["clarify_id"]]["status"] == "confirmed"
            assert r.status_code == 200

    def test_resume_amended_returns_new_card(self):
        """confirmed=False (amended) should return the updated card JSON,
        not an SSE stream."""
        s = self._create_session("看下销售")
        c = self._client()
        import app.agent.nodes.merge_clarification as mcm
        with patch.object(
            mcm, "apply_clarification_to_plan",
            return_value={
                "question": "看下销售", "measures": [],
                "confidence": 0.7, "notes": [],
            },
        ):
            r = c.post(
                f"/api/v1/clarify/{s['clarify_id']}/resume",
                json={
                    "response_type": "selection",
                    "selections": {"measure": "GMV"},
                    "confirmed": False,
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "amended"
            assert body["rounds"] == 2
            assert "suggestions" in body
