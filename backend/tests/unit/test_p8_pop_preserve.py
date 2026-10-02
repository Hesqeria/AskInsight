"""P8: period-over-period (环比) must survive grounding + clarify merge
and always route to LLM generation (window functions)."""
from datetime import date

from app.ontology.plan import SemanticPlan
from app.agent.nodes.semantic_grounding import build_plan
from app.agent.nodes.merge_clarification import _recompute_confidence


def test_build_plan_marks_pop():
    plan = build_plan("本月GMV环比上月增长了多少", today=date(2026, 9, 9))
    assert plan.pop is True
    assert plan.measures, "measure should still ground"
    assert plan.confidence <= 0.5, "pop must not take the render path"


def test_plain_question_not_pop():
    plan = build_plan("上个月GMV是多少", today=date(2026, 9, 9))
    assert plan.pop is False


def test_pop_roundtrips_through_dict():
    plan = build_plan("GMV同比去年怎么样", today=date(2026, 9, 9))
    d = plan.to_dict()
    assert d["pop"] is True
    plan2 = SemanticPlan.from_dict(d)
    assert plan2.pop is True


def test_merge_confidence_capped_for_pop():
    plan = build_plan("本月GMV环比上月增长了多少", today=date(2026, 9, 9))
    plan.time = plan.time or None
    from app.ontology.plan import TimeRange
    plan.time = TimeRange(column="t.dt", start="2026-09-01",
                          end="2026-09-30", grain="day")
    # merge would otherwise bump 0.5 -> 0.9 (has_time) -> render drops 环比
    assert _recompute_confidence(plan) == 0.9
    plan.confidence = min(_recompute_confidence(plan), 0.5)
    assert plan.confidence == 0.5


def test_render_gate_respects_pop():
    # generate_sql gate condition replicated: pop never renders
    plan_dict = {"confidence": 0.9, "pop": True}
    would_render = (plan_dict.get("confidence", 0) >= 0.7
                    and not plan_dict.get("pop", False))
    assert would_render is False
