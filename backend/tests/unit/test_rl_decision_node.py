"""Decision insight RL integration tests.

Verifies:
  - decision_insight node picks a policy via Thompson Sampling
  - the chosen policy is recorded with request_id for reward attribution
  - fallback to `facts_first` when rl_repository is None or raises
  - full reward loop: rating -> Beta posterior update
"""
import asyncio
import random
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.rl import PolicyStats, ThompsonSampler, ACTIVE_POLICIES, POLICY_INSTRUCTIONS


def _make_runtime(context: dict):
    """Build a fake langgraph Runtime with stream_writer + context."""
    runtime = MagicMock()
    runtime.stream_writer = lambda x: None
    runtime.context = context
    return runtime


def _patch_llm(monkeypatch, response_text="test insight"):
    """Replace the module-level `llm` with a stub. LangChain's
    ChatOpenAI is a pydantic model so we can't monkeypatch its method
    directly; swap the whole object instead."""
    from app.agent.nodes import decision_insight as mod

    class _FakeLLM:
        async def ainvoke(self, messages):
            r = MagicMock()
            r.content = response_text
            return r

    monkeypatch.setattr(mod, "llm", _FakeLLM())


@pytest.mark.asyncio
async def test_node_picks_policy_via_bandit(monkeypatch):
    """The node must call the sampler and pass the policy's instruction
    to the LLM (as a SystemMessage)."""
    from app.agent.nodes import decision_insight as mod
    from app.rl import POLICY_INSTRUCTIONS, ACTIVE_POLICIES

    # Pin the RNG so selection is deterministic.
    pinned_sampler = mod.ThompsonSampler(rng=random.Random(7))
    captured_messages = []

    class _CapturingLLM:
        async def ainvoke(self, messages):
            captured_messages.extend(messages)
            r = MagicMock(); r.content = "insight"
            return r

    monkeypatch.setattr(mod, "llm", _CapturingLLM())

    rl_repo = AsyncMock()
    rl_repo.get_all_stats.return_value = {
        name: PolicyStats(policy_name=name) for name in ACTIVE_POLICIES
    }
    rl_repo.log_decision.return_value = None

    state = {
        "query": "GMV 多少 帮我分析",
        "_last_result": [{"gmv": 100}, {"gmv": 200}, {"gmv": 300}],
        "anomaly_result": {}, "attribution_result": {}, "drill_down_result": {},
    }
    runtime = _make_runtime({
        "rl_repository": rl_repo,
        "rl_sampler": pinned_sampler,
    })

    result = await mod.decision_insight(state, runtime)

    # Policy was recorded.
    assert result["decision_policy"] in ACTIVE_POLICIES
    assert result["decision_insights"] == "insight"

    # SystemMessage contains the policy's instruction.
    from langchain_core.messages import SystemMessage
    sys_msgs = [m for m in captured_messages if isinstance(m, SystemMessage)]
    assert sys_msgs, "expected a SystemMessage with the policy instruction"
    chosen_policy = result["decision_policy"]
    assert POLICY_INSTRUCTIONS[chosen_policy] in sys_msgs[0].content

    # log_decision was called with the chosen policy + request_id.
    rl_repo.log_decision.assert_awaited_once()
    call_kwargs = rl_repo.log_decision.call_args.kwargs
    assert call_kwargs["policy_name"] == chosen_policy
    assert call_kwargs["insight"] == "insight"
    assert "request_id" in call_kwargs


@pytest.mark.asyncio
async def test_node_falls_back_when_rl_repo_none(monkeypatch):
    """Without an rl_repository, the node uses `facts_first` and never
    crashes (graceful degradation on dev boxes / pre-DDL)."""
    from app.agent.nodes import decision_insight as mod
    _patch_llm(monkeypatch)

    state = {"query": "q 为什么", "_last_result": []}
    runtime = _make_runtime({})  # no rl_repository

    result = await mod.decision_insight(state, runtime)
    assert result["decision_policy"] == "facts_first"
    assert result["decision_insights"] == "test insight"


@pytest.mark.asyncio
async def test_node_falls_back_when_repo_raises(monkeypatch):
    """A repo exception must not break the pipeline; we still produce
    a decision_insight (with facts_first policy)."""
    from app.agent.nodes import decision_insight as mod
    _patch_llm(monkeypatch)

    rl_repo = AsyncMock()
    rl_repo.get_all_stats.side_effect = RuntimeError("DB down")
    # log_decision may or may not be called - if it is, it shouldn't
    # raise. We assert only on the return value.
    rl_repo.log_decision.return_value = None

    state = {"query": "q 为什么", "_last_result": [{"gmv": 1}, {"gmv": 2}, {"gmv": 3}]}
    runtime = _make_runtime({"rl_repository": rl_repo})

    result = await mod.decision_insight(state, runtime)
    assert result["decision_policy"] == "facts_first"
    assert result["decision_insights"] == "test insight"


@pytest.mark.asyncio
async def test_reward_loop_updates_beta_posterior():
    """End-to-end RL loop: a positive rating for a policy must increase
    its alpha (and its expected_reward)."""
    from app.rl import ThompsonSampler, PolicyStats

    # Initial posterior: α=2, β=2 -> expected_reward = 0.5
    initial = PolicyStats(policy_name="action_first",
                          alpha=2.0, beta=2.0,
                          total_calls=10, positive_rewards=2, negative_rewards=8)

    # User gave a positive rating (reward=1).
    after_pos = ThompsonSampler.update_stats(initial, reward=1.0)
    assert after_pos.alpha == 3.0
    assert after_pos.beta == 2.0
    assert after_pos.expected_reward > initial.expected_reward  # 0.6 > 0.5

    # Negative rating (reward=0) on the original.
    after_neg = ThompsonSampler.update_stats(initial, reward=0.0)
    assert after_neg.alpha == 2.0
    assert after_neg.beta == 3.0
    assert after_neg.expected_reward < initial.expected_reward  # 0.4 < 0.5


@pytest.mark.asyncio
async def test_reward_endpoint_closes_loop(monkeypatch):
    """The /api/quality/rate endpoint must look up the policy used for
    request_id and apply the Bayesian update via the repository."""
    from app.api.routers import feedback_router as router
    from app.rl import PolicyStats

    # Fake session + repo: pre-seed a decision + a policy posterior.
    fake_session = AsyncMock()
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()

    # Patch RlDorisRepository to return controlled values.
    fake_rl_repo = AsyncMock()
    fake_rl_repo.get_decision_by_request.return_value = {
        "policy_name": "action_first",
        "query": "GMV",
        "insight": "do X",
    }
    fake_rl_repo.get_all_stats.return_value = {
        "action_first": PolicyStats(policy_name="action_first",
                                    alpha=2.0, beta=2.0,
                                    total_calls=10, positive_rewards=2,
                                    negative_rewards=8),
    }
    upsert_calls = []
    async def capture_upsert(stats):
        upsert_calls.append(stats)
    fake_rl_repo.upsert_stats = capture_upsert

    monkeypatch.setattr(
        "app.repositories.doris.rl.rl_doris_repository.RlDorisRepository",
        lambda session: fake_rl_repo,
    )

    body = router.QualitySchema(request_id="req-001", rating=1, comment="great")
    user = {"sub": "tester", "role": "L2_analyst"}

    result = await router.rate_answer(body, user=user, session=fake_session)

    assert result["status"] == "ok"
    assert result["rl_updated"] is True
    assert result["policy"] == "action_first"
    # Alpha increased from 2 -> 3 (positive reward).
    assert len(upsert_calls) == 1
    assert upsert_calls[0].alpha == 3.0
    assert upsert_calls[0].beta == 2.0


@pytest.mark.asyncio
async def test_reward_endpoint_handles_unknown_request_id(monkeypatch):
    """If no decision was logged for this request_id, the rating is
    still recorded but rl_updated=False."""
    from app.api.routers import feedback_router as router

    fake_session = AsyncMock()
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()

    fake_rl_repo = AsyncMock()
    fake_rl_repo.get_decision_by_request.return_value = None

    monkeypatch.setattr(
        "app.repositories.doris.rl.rl_doris_repository.RlDorisRepository",
        lambda session: fake_rl_repo,
    )

    body = router.QualitySchema(request_id="unknown-req", rating=1)
    user = {"sub": "tester", "role": "L2_analyst"}

    result = await router.rate_answer(body, user=user, session=fake_session)

    assert result["status"] == "ok"
    assert result["rl_updated"] is False
    # Repo stats lookup must not even happen.
    fake_rl_repo.get_all_stats.assert_not_called()
