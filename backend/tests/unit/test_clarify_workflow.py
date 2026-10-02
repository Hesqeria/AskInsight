"""Tests for clarify workflow (PRD: 模糊提问澄清交互).

Covers:
  - needs_clarification / detect_missing_fields (pure)
  - build_suggestions (pure)
  - apply_clarification_to_plan (pure, the core merge logic)
  - ClarifyRepository (integration, requires Doris)
  - End-to-end: ambiguous NL → low-confidence plan → ask → merge → execute-ready plan
"""
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.nodes.ask_clarification import (
    needs_clarification, detect_missing_fields, build_suggestions,
    CLARIFY_CONFIDENCE_THRESHOLD,
)
from app.agent.nodes.merge_clarification import apply_clarification_to_plan
from app.repositories.doris.clarify.clarify_repository import (
    ClarifySession, ClarifyRepository,
)


# --------------------------------------------------------------------------- #
# Test fixtures: representative plan shapes
# --------------------------------------------------------------------------- #
def _plan(measures=True, time=True, dims=True, confidence=1.0):
    """Build a SemanticPlan dict for testing."""
    return {
        "question": "test",
        "measures": [{
            "business_term": "GMV",
            "class_id": "C030",
            "column": "dw.dwd_order_info_inc.total_amount",
            "aggregation": "SUM",
            "pre_filters": ["order_status = 'PAID'"],
        }] if measures else [],
        "dimensions": [{
            "class_id": "C050", "column": "r.region_name",
            "operator": "=", "value": "华北",
        }] if dims else [],
        "group_by": [],
        "time": {
            "column": "t.dt", "start": "2026-07-01", "end": "2026-07-31",
        } if time else None,
        "joins": [],
        "confidence": confidence,
        "grounding_source": "rule",
        "notes": [],
    }


# --------------------------------------------------------------------------- #
# needs_clarification / detect_missing_fields
# --------------------------------------------------------------------------- #
class TestNeedsClarification:
    def test_high_confidence_no_missing(self):
        state = {"semantic_plan": _plan(confidence=1.0)}
        assert needs_clarification(state) is False

    def test_low_confidence_triggers(self):
        state = {"semantic_plan": _plan(confidence=0.5)}
        assert needs_clarification(state) is True

    def test_threshold_boundary(self):
        # Exactly 0.7 should NOT trigger.
        state = {"semantic_plan": _plan(confidence=0.7)}
        assert needs_clarification(state) is False

    def test_below_threshold_triggers(self):
        state = {"semantic_plan": _plan(confidence=0.69)}
        assert needs_clarification(state) is True

    def test_missing_time_with_high_confidence_triggers(self):
        # Even at high confidence, missing time field triggers clarification.
        plan = _plan(time=False, confidence=1.0)
        state = {"semantic_plan": plan}
        assert needs_clarification(state) is True

    def test_no_plan_does_not_trigger(self):
        # When no plan exists (LLM path), fall through to generate_sql.
        assert needs_clarification({"semantic_plan": None}) is False
        assert needs_clarification({}) is False


class TestDetectMissingFields:
    def test_no_measures_reports_measure_missing(self):
        plan = _plan(measures=False, confidence=0.3)
        missing = detect_missing_fields(plan)
        assert any(m["field"] == "measure" for m in missing)

    def test_no_time_reports_time_missing(self):
        plan = _plan(time=False, confidence=1.0)
        missing = detect_missing_fields(plan)
        assert any(m["field"] == "time" for m in missing)

    def test_no_dims_low_confidence_reports_dimension(self):
        plan = _plan(dims=False, confidence=0.5)
        missing = detect_missing_fields(plan)
        assert any(m["field"] == "dimension" for m in missing)

    def test_full_plan_no_missing(self):
        plan = _plan(confidence=1.0)
        assert detect_missing_fields(plan) == []

    def test_empty_plan_returns_empty(self):
        assert detect_missing_fields({}) == []
        assert detect_missing_fields(None) == []


# --------------------------------------------------------------------------- #
# build_suggestions
# --------------------------------------------------------------------------- #
class TestBuildSuggestions:
    def test_missing_measure_yields_measure_group(self):
        plan = _plan(measures=False, confidence=0.3)
        missing = [{"field": "measure", "reason": "missing", "current_value": None}]
        groups = build_suggestions(plan, missing)
        measure_group = next(g for g in groups if g["field"] == "measure")
        assert len(measure_group["options"]) >= 1
        labels = [o["label"] for o in measure_group["options"]]
        assert any("GMV" in lab for lab in labels)
        # GMV should be marked recommended.
        gmv_opt = next(o for o in measure_group["options"] if o["value"] == "GMV")
        assert gmv_opt["recommended"] is True

    def test_missing_time_yields_time_group(self):
        plan = _plan(time=False, confidence=0.7)
        missing = [{"field": "time", "reason": "missing"}]
        groups = build_suggestions(plan, missing)
        time_group = next(g for g in groups if g["field"] == "time")
        # Should include common time options.
        labels = [o["label"] for o in time_group["options"]]
        assert "上月" in labels
        assert "近7天" in labels
        assert time_group["multi_select"] is False

    def test_missing_dimension_yields_group_by_group(self):
        plan = _plan(dims=False, confidence=0.5)
        missing = [{"field": "dimension", "reason": "low_confidence"}]
        groups = build_suggestions(plan, missing)
        gb_group = next(g for g in groups if g["field"] == "group_by")
        assert gb_group["multi_select"] is True  # multi-select allowed
        labels = [o["label"] for o in gb_group["options"]]
        assert "按地区" in labels
        assert "按商品" in labels

    def test_no_missing_returns_empty(self):
        assert build_suggestions(_plan(), []) == []


# --------------------------------------------------------------------------- #
# apply_clarification_to_plan — the core merge logic
# --------------------------------------------------------------------------- #
class TestApplyClarificationToPlan:
    def test_user_selects_measure_replaces_measures(self):
        """User picked 'order_count' instead of the default 'GMV'."""
        initial = _plan(measures=False, confidence=0.3)
        response = {"selections": {"measure": "order_count"}}
        merged = apply_clarification_to_plan(initial, response)
        assert len(merged["measures"]) == 1
        assert merged["measures"][0]["business_term"] == "order_count"
        # Region dim (initial plan carries C050) + ads_total measure ->
        # adapt_measure_source re-sources to raw dwd: COUNT(id) + paid filter
        assert merged["measures"][0]["aggregation"] == "COUNT"
        assert merged["measures"][0]["column"] == "dw.dwd_order_info_inc.id"
        assert "order_status = '1001'" in merged["measures"][0]["pre_filters"]

    def test_user_selects_time_parses_correctly(self):
        """User picked '上月' — should resolve to last month's date range."""
        initial = _plan(time=False, confidence=0.7)
        response = {"selections": {"time": "上月"}}
        merged = apply_clarification_to_plan(
            initial, response, today=date(2026, 8, 12),
        )
        assert merged["time"] is not None
        assert merged["time"]["start"] == "2026-07-01"
        assert merged["time"]["end"] == "2026-07-31"

    def test_user_selects_group_by_adds_join(self):
        """User picked '按地区' — should add C050 group_by + 2-hop join."""
        initial = _plan(dims=False, confidence=0.5)
        initial["joins"] = []  # clean slate
        response = {"selections": {"group_by": ["C050"]}}
        merged = apply_clarification_to_plan(initial, response)
        assert len(merged["group_by"]) == 1
        assert merged["group_by"][0]["class_id"] == "C050"
        # JOIN_REGISTRY[("C030","C050")] has 2 hops.
        assert len(merged["joins"]) == 2

    def test_confidence_bumped_after_full_clarification(self):
        """Plan with confidence=0.5 → user fills everything → 1.0."""
        initial = _plan(time=False, dims=False, confidence=0.5)
        response = {
            "selections": {
                "time": "上月",
                "group_by": ["C050"],
            }
        }
        merged = apply_clarification_to_plan(
            initial, response, today=date(2026, 8, 12),
        )
        assert merged["confidence"] == 1.0
        assert merged["grounding_source"] == "hybrid"

    def test_partial_clarification_partial_confidence(self):
        """User only picked time, no group_by → 0.9."""
        initial = _plan(time=False, dims=False, confidence=0.5)
        response = {"selections": {"time": "上月"}}
        merged = apply_clarification_to_plan(
            initial, response, today=date(2026, 8, 12),
        )
        # measure + time but no group_by → 0.9 per _recompute_confidence
        assert merged["confidence"] == 0.9

    def test_unknown_measure_ignored_with_note(self):
        initial = _plan(measures=False, confidence=0.3)
        response = {"selections": {"measure": "nonexistent_term"}}
        merged = apply_clarification_to_plan(initial, response)
        assert len(merged["measures"]) == 0
        assert any("nonexistent_term" in n for n in merged["notes"])

    def test_invalid_time_text_added_to_notes(self):
        initial = _plan(time=False, confidence=0.7)
        response = {"selections": {"time": "totally invalid phrase"}}
        merged = apply_clarification_to_plan(initial, response)
        assert merged["time"] is None
        assert any("could not parse" in n for n in merged["notes"])

    def test_input_plan_not_mutated(self):
        initial = _plan(time=False, confidence=0.7)
        original_dict = dict(initial)
        response = {"selections": {"time": "昨天"}}
        apply_clarification_to_plan(initial, response, today=date(2026, 8, 12))
        # Original should be unchanged.
        assert initial == original_dict

    def test_empty_response_returns_initial_with_lower_confidence(self):
        initial = _plan(time=False, confidence=0.7)
        merged = apply_clarification_to_plan(initial, {})
        # No selections → nothing changed, but source now 'hybrid'.
        assert merged["time"] is None
        assert merged["grounding_source"] == "hybrid"


# --------------------------------------------------------------------------- #
# ClarifyRepository (integration, requires Doris clarify_session table)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.skipif(not __import__("os").getenv("RUN_DORIS_INTEGRATION"),
                    reason="integration: set RUN_DORIS_INTEGRATION=1 to run")
class TestClarifyRepository:
    """Integration tests against the live Doris.

    Marked as 'integration' so unit-test runs skip these by default.
    Run with: pytest -m integration
    """
    @pytest.fixture
    async def session(self):
        import os as _os
        if not _os.getenv("RUN_DORIS_INTEGRATION"):
            pytest.skip("integration: set RUN_DORIS_INTEGRATION=1 to run")
        from sqlalchemy.ext.asyncio import create_async_engine
        import os as _os
        engine = create_async_engine(
            f"mysql+asyncmy://{_os.getenv('DORIS_USER', 'root')}:"
            f"{_os.getenv('DORIS_PASSWORD', '')}@"
            f"{_os.getenv('DORIS_HOST', '192.168.137.52')}:9030/data_agent"
        )
        async with engine.connect() as conn:
            from sqlalchemy.ext.asyncio import AsyncSession
            async with AsyncSession(conn) as s:
                yield s
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_create_and_get(self, session):
        repo = ClarifyRepository(session)
        s = await repo.create(
            question="测试问题",
            initial_plan={"question": "测试问题", "confidence": 0.5},
            missing_fields=[{"field": "time", "reason": "missing"}],
            suggestions_given=[{"field": "time", "options": []}],
            username="test_user",
        )
        assert s is not None
        assert s.status == "pending"
        # Get it back.
        fetched = await repo.get(s.clarify_id)
        assert fetched is not None
        assert fetched.question == "测试问题"
        assert fetched.initial_plan["confidence"] == 0.5

    @pytest.mark.asyncio
    async def test_update_response(self, session):
        repo = ClarifyRepository(session)
        s = await repo.create(
            question="update test",
            initial_plan={"confidence": 0.5},
            missing_fields=[],
            suggestions_given=[],
        )
        updated = await repo.update_response(
            clarify_id=s.clarify_id,
            user_response={"selections": {"time": "昨天"}},
            final_plan={"confidence": 1.0},
            status="confirmed",
        )
        assert updated is not None
        assert updated.status == "confirmed"
        assert updated.final_plan["confidence"] == 1.0
        assert updated.user_response["selections"]["time"] == "昨天"


# --------------------------------------------------------------------------- #
# End-to-end: ambiguous NL → ask → merge → execute-ready
# --------------------------------------------------------------------------- #
class TestEndToEndFlow:
    """Simulates the full PRD flow:

    1. User asks "看下销售" (vague)
    2. semantic_grounding produces confidence=0.3 (no measure matched)
    3. ask_clarification suggests measure/time/group_by options
    4. User picks "GMV" + "上月" + "按地区"
    5. apply_clarification_to_plan merges → confidence=1.0
    6. Plan is now ready for render_sql_from_plan
    """
    def test_vague_question_full_flow(self):
        from app.ontology.plan import render_sql_from_plan, SemanticPlan, Measure, DimensionFilter, DimensionGroupBy, TimeRange, JoinSpec

        # Step 1-2: initial low-confidence plan (no measure matched)
        initial_plan = {
            "question": "看下销售",
            "measures": [],
            "dimensions": [],
            "group_by": [],
            "time": None,
            "joins": [],
            "confidence": 0.3,
            "grounding_source": "rule",
            "notes": ["no measure matched in registry"],
        }

        # Step 3: build suggestions
        missing = detect_missing_fields(initial_plan)
        assert any(m["field"] == "measure" for m in missing)
        suggestions = build_suggestions(initial_plan, missing)
        assert any(g["field"] == "measure" for g in suggestions)

        # Step 4: user picks options
        user_response = {
            "selections": {
                "measure": "GMV",
                "time": "上月",
                "group_by": ["C050"],
            },
            "confirmed": True,
        }

        # Step 5: merge
        merged = apply_clarification_to_plan(
            initial_plan, user_response, today=date(2026, 8, 12),
        )
        assert merged["confidence"] == 1.0
        assert len(merged["measures"]) == 1
        assert merged["measures"][0]["business_term"] == "GMV"
        assert merged["time"]["start"] == "2026-07-01"
        assert merged["time"]["end"] == "2026-07-31"
        assert len(merged["group_by"]) == 1
        assert merged["group_by"][0]["column"] == "r.region_name"
        # Region GMV re-sourced to ads_region_gmv_rank: no join chain
        assert len(merged["joins"]) == 2  # province + region chain

        # Step 6: render SQL
        from app.agent.nodes.generate_sql import _dict_to_plan
        plan_obj = _dict_to_plan(merged)
        sql = render_sql_from_plan(plan_obj)
        assert "SUM(t.total_amount)" in sql
        assert "FROM dw.dwd_order_info_inc" in sql
        assert "BETWEEN '2026-07-01' AND '2026-07-31'" in sql
        assert "GROUP BY r.region_name" in sql
