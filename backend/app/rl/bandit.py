"""Thompson Sampling Bandit for the decision_insight node.

This is the **decision-time policy layer** of RL: instead of trying to
fine-tune the LLM (PPO/DPO, which needs GPU + offline training), we
maintain a small set of *prompt strategies* and learn online which one
the user actually prefers, judged by the existing `/api/quality/rate`
binary reward.

Algorithm
---------
Each policy π_i has a Beta(α_i, β_i) posterior over its click-through
rate (probability of producing a satisfying insight). For each request:

    1. Sample θ_i ~ Beta(α_i, β_i) for every policy.
    2. Pick argmax θ_i (Thompson Sampling).
    3. Generate the insight with that policy.
    4. When the user rates the answer (0/1), update the chosen policy's
       α += reward, β += (1 - reward).

The Beta(1, 1) prior is uniform - all policies get a fair initial
chance. Posterior updates are O(1) and require no gradient math.

Why this is enough
------------------
- Decision-making quality in this product is dominated by *prompt
  strategy choice*, not LLM weight updates. A good 8B model with the
  right prompt beats a generic 70B model with a default prompt.
- Reward signal is binary and noisy. Bandit needs ~50 samples per
  policy to distinguish a 0.6-CTR policy from a 0.4-CTR policy at
  p<0.05; PPO would need 10-100x more.
- Stateless at inference time - no model checkpoint to load.
"""
import random
from dataclasses import dataclass
from typing import Optional


# --------------------------------------------------------------------------
# Policy library
# --------------------------------------------------------------------------
# Each policy is a *style instruction* prepended to the shared DECISION_PROMPT.
# They are intentionally orthogonal so the bandit has meaningful choices:
#   facts_first  - default behavior (data first, exhaustive)
#   action_first - lead with actionable recommendations
#   risk_focus   - lead with risks/caveats/anomalies
#   executive    - concise, top-down, summary-first
#
# Adding a new policy = add an entry here + seed it in the policy_stats table.
# The bandit will start exploring it automatically (Beta(1,1) uniform prior).

POLICY_INSTRUCTIONS: dict[str, str] = {
    "facts_first": (
        "请先客观陈述数据要点,再依次列出归因、风险、行动建议。"
        "格式: 数据要点 → 归因分析 → 风险提示 → 建议行动。"
    ),
    "action_first": (
        "请优先给出可立即执行的建议(2-4 条,每条含负责方/预期效果/时限),"
        "再补充数据依据。格式: 推荐行动(表格) → 数据支撑 → 风险。"
    ),
    "risk_focus": (
        "请优先提示风险与潜在问题(尤其是异常数据/指标偏离/未覆盖维度),"
        "再给缓解措施。格式: 关键风险 → 数据证据 → 缓解建议。"
    ),
    "executive": (
        "请用高管视角输出: 一句话结论 + 3 个关键数字 + 一条最重要建议。"
        "总字数控制在 150 字以内,避免技术细节。"
    ),
}

# Policies that get explored. Subset of POLICY_INSTRUCTIONS.keys().
ACTIVE_POLICIES = list(POLICY_INSTRUCTIONS.keys())

# Beta(1,1) = uniform prior; default stats for a brand-new policy.
DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 1.0


@dataclass
class PolicyStats:
    """In-memory snapshot of one policy's posterior."""
    policy_name: str
    alpha: float = DEFAULT_ALPHA
    beta: float = DEFAULT_BETA
    total_calls: int = 0
    positive_rewards: int = 0
    negative_rewards: int = 0

    @property
    def expected_reward(self) -> float:
        """Posterior mean α/(α+β) - the policy's estimated CTR."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def samples_per_call(self) -> float:
        return self.positive_rewards / max(1, self.total_calls)


class ThompsonSampler:
    """Pure-Python Thompson Sampling over a fixed policy set.

    Stateless aside from a random.RNG; the posterior state lives in the
    `rl_policy_stats` table (see RlDorisRepository). This separation
    makes the sampler trivially testable and lets multiple worker
    processes share the same posterior via the DB."""

    def __init__(self, policies: Optional[list[str]] = None, rng: Optional[random.Random] = None):
        self.policies = list(policies) if policies is not None else list(ACTIVE_POLICIES)
        # Inject an RNG so tests can pin the seed; default to the stdlib
        # global for production determinism with the OS entropy source.
        self._rng = rng or random

    def select(self, stats_by_policy: dict[str, PolicyStats]) -> tuple[str, float]:
        """Pick the policy with the highest Thompson sample.

        Args:
            stats_by_policy: {policy_name: PolicyStats}. Missing policies
                fall back to the Beta(1,1) uniform prior (i.e. they still
                get sampled, encouraging exploration of new policies).

        Returns:
            (chosen_policy, sampled_theta) - the sampled theta is useful
            for observability and as a confidence signal.
        """
        best_policy = None
        best_theta = -1.0
        for name in self.policies:
            stats = stats_by_policy.get(name) or PolicyStats(policy_name=name)
            theta = self._rng.betavariate(stats.alpha, stats.beta)
            if theta > best_theta:
                best_theta = theta
                best_policy = name
        # `policies` is non-empty by construction, but be defensive.
        if best_policy is None:
            best_policy = self.policies[0]
            best_theta = self._rng.betavariate(DEFAULT_ALPHA, DEFAULT_BETA)
        return best_policy, best_theta

    @staticmethod
    def update_stats(stats: PolicyStats, reward: float) -> PolicyStats:
        """Bayesian update for a Beta-Bernoulli posterior. Reward must be
        in [0, 1] (binary or fractional). Returns a NEW PolicyStats - the
        caller is responsible for persisting it."""
        if reward < 0 or reward > 1:
            raise ValueError(f"reward must be in [0,1], got {reward}")
        new_stats = PolicyStats(
            policy_name=stats.policy_name,
            alpha=stats.alpha + reward,
            beta=stats.beta + (1.0 - reward),
            total_calls=stats.total_calls + 1,
            positive_rewards=stats.positive_rewards + (1 if reward >= 0.5 else 0),
            negative_rewards=stats.negative_rewards + (0 if reward >= 0.5 else 1),
        )
        return new_stats


def get_policy_instruction(policy_name: str) -> str:
    """Return the system-instruction text for a policy."""
    return POLICY_INSTRUCTIONS.get(policy_name, POLICY_INSTRUCTIONS["facts_first"])
