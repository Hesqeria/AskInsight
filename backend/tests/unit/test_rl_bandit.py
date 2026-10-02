"""Thompson Sampling bandit tests.

Covers:
  - Beta sampling picks argmax theta
  - Bayesian update shifts the posterior correctly
  - Convergence behavior (a clearly better policy gets picked more often)
  - Reward validation
  - Policy instruction lookup + fallback
"""
import random

import pytest

from app.rl import (
    PolicyStats, ThompsonSampler, POLICY_INSTRUCTIONS,
    ACTIVE_POLICIES, get_policy_instruction,
    DEFAULT_ALPHA, DEFAULT_BETA,
)


# --------------------------------------------------------------------------- #
# Beta sampling
# --------------------------------------------------------------------------- #
def test_select_picks_argmax_theta():
    """The sampler must pick the policy with the highest sampled value
    (not the highest posterior mean). This pins the Thompson semantics."""
    rng = random.Random(42)
    # Force known samples by pre-rolling the RNG.
    expected = [rng.betavariate(1, 1) for _ in ACTIVE_POLICIES]
    # Reset and replay.
    rng2 = random.Random(42)
    sampler = ThompsonSampler(rng=rng2)
    stats = {n: PolicyStats(policy_name=n) for n in ACTIVE_POLICIES}
    chosen, theta = sampler.select(stats)
    assert chosen == ACTIVE_POLICIES[expected.index(max(expected))]
    assert abs(theta - max(expected)) < 1e-9


def test_select_uniform_prior_visits_all_policies():
    """With Beta(1,1) priors, every policy must get selected at least
    once over a long run - otherwise exploration is broken."""
    sampler = ThompsonSampler(rng=random.Random(0))
    stats = {n: PolicyStats(policy_name=n) for n in ACTIVE_POLICIES}
    seen = set()
    for _ in range(500):
        chosen, _ = sampler.select(stats)
        seen.add(chosen)
    assert seen == set(ACTIVE_POLICIES)


def test_missing_policy_falls_back_to_prior():
    """A policy not present in `stats` must still get sampled using the
    Beta(1,1) uniform prior so new policies are explored automatically."""
    sampler = ThompsonSampler(rng=random.Random(123))
    # Empty stats dict - all policies get prior.
    chosen, _ = sampler.select({})
    assert chosen in ACTIVE_POLICIES


def test_select_with_empty_policy_list_is_safe():
    """Defensive: an empty policy list must not crash."""
    sampler = ThompsonSampler(policies=["only_one"])
    chosen, _ = sampler.select({})
    assert chosen == "only_one"


# --------------------------------------------------------------------------- #
# Bayesian update
# --------------------------------------------------------------------------- #
def test_update_positive_reward_increases_alpha():
    s = PolicyStats(policy_name="p", alpha=2.0, beta=3.0, total_calls=5,
                    positive_rewards=2, negative_rewards=3)
    new_s = ThompsonSampler.update_stats(s, reward=1.0)
    assert new_s.alpha == 3.0          # alpha += reward
    assert new_s.beta == 3.0           # unchanged
    assert new_s.total_calls == 6
    assert new_s.positive_rewards == 3
    assert new_s.negative_rewards == 3


def test_update_negative_reward_increases_beta():
    s = PolicyStats(policy_name="p", alpha=2.0, beta=3.0, total_calls=5,
                    positive_rewards=2, negative_rewards=3)
    new_s = ThompsonSampler.update_stats(s, reward=0.0)
    assert new_s.alpha == 2.0
    assert new_s.beta == 4.0
    assert new_s.total_calls == 6
    assert new_s.positive_rewards == 2
    assert new_s.negative_rewards == 4


def test_update_validates_reward_range():
    s = PolicyStats(policy_name="p")
    with pytest.raises(ValueError):
        ThompsonSampler.update_stats(s, reward=-0.1)
    with pytest.raises(ValueError):
        ThompsonSampler.update_stats(s, reward=1.5)


def test_update_does_not_mutate_input():
    """update_stats returns a NEW PolicyStats - input must be untouched
    so the caller can persist atomically."""
    s = PolicyStats(policy_name="p", alpha=2.0, beta=3.0, total_calls=5)
    _ = ThompsonSampler.update_stats(s, reward=1.0)
    assert s.alpha == 2.0
    assert s.total_calls == 5


# --------------------------------------------------------------------------- #
# Convergence (the actual point of using a bandit)
# --------------------------------------------------------------------------- #
def test_better_policy_wins_over_time():
    """When one policy is genuinely better (higher reward rate), Thompson
    Sampling should select it more often than chance after seeing data."""
    rng = random.Random(2024)
    sampler = ThompsonSampler(rng=rng)

    # Simulate two policies with different true CTRs.
    # "good" -> 0.7 CTR, "bad" -> 0.2 CTR.
    # Start from priors, accumulate stats over 200 simulated rounds.
    stats = {
        "facts_first": PolicyStats(policy_name="facts_first",
                                    alpha=1, beta=1),  # will be the "good" one
        "action_first": PolicyStats(policy_name="action_first",
                                     alpha=1, beta=1),  # "bad" one
    }
    # Restrict the sampler to just these two for the test.
    sampler.policies = ["facts_first", "action_first"]

    # Simulated ground-truth reward rates per policy.
    true_ctr = {"facts_first": 0.7, "action_first": 0.2}

    for _ in range(200):
        chosen, _ = sampler.select(stats)
        reward = 1.0 if rng.random() < true_ctr[chosen] else 0.0
        stats[chosen] = ThompsonSampler.update_stats(stats[chosen], reward)

    # After 200 rounds, the better policy should have higher expected reward
    # AND should have been called more often.
    assert stats["facts_first"].expected_reward > stats["action_first"].expected_reward
    assert stats["facts_first"].total_calls > stats["action_first"].total_calls
    # And the learned estimate should be close to the true CTR (within 0.15).
    assert abs(stats["facts_first"].expected_reward - 0.7) < 0.15
    assert abs(stats["action_first"].expected_reward - 0.2) < 0.15


# --------------------------------------------------------------------------- #
# Policy library
# --------------------------------------------------------------------------- #
def test_policy_instructions_are_nonempty():
    for name, instr in POLICY_INSTRUCTIONS.items():
        assert isinstance(instr, str) and len(instr) > 10, name


def test_get_policy_instruction_known():
    for name in ACTIVE_POLICIES:
        assert get_policy_instruction(name) == POLICY_INSTRUCTIONS[name]


def test_get_policy_instruction_unknown_falls_back():
    """Unknown policy falls back to facts_first, never raises."""
    assert get_policy_instruction("does_not_exist") == POLICY_INSTRUCTIONS["facts_first"]


def test_default_priors_are_uniform():
    assert DEFAULT_ALPHA == 1.0
    assert DEFAULT_BETA == 1.0


def test_expected_reward_property():
    """PolicyStats.expected_reward must be alpha / (alpha+beta)."""
    s = PolicyStats(policy_name="p", alpha=3, beta=1)
    assert s.expected_reward == pytest.approx(0.75)
    s2 = PolicyStats(policy_name="p", alpha=1, beta=1)
    assert s2.expected_reward == pytest.approx(0.5)
