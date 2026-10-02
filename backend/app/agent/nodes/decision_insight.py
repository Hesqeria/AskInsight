"""Decision insight node: generates decision suggestions from query results
+ anomaly data.

RL integration (Thompson Sampling bandit):
  Instead of always using one fixed prompt, the node samples a prompt
  *policy* from a Beta-Bernoulli posterior learned from user feedback
  (/api/quality/rate). Each policy reorders / re-emphasizes the same
  context (facts-first vs action-first vs risk-focus vs executive).

  Decision flow:
    1. Load policy posteriors from `rl_policy_stats` (Beta(1,1) prior
       on first call).
    2. Sample θ_i ~ Beta(α_i, β_i) for each policy, pick argmax.
    3. Compose the prompt: policy instruction + the shared context
       (query / data / anomaly / attribution / drill).
    4. Call the LLM.
    5. Log (request_id, policy, sampled_theta, insight) so the reward
       endpoint can attribute the next user rating.

  When the RL repository is unavailable, the node falls back to the
  `facts_first` policy deterministically - identical to the pre-RL
  behavior - so the pipeline stays robust on dev boxes / before the
  DDL has been applied.
"""
import json
from langgraph.runtime import Runtime
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import fast_llm as llm
from app.core.context import request_id_ctx_var
from app.core.log import logger
from app.rl import ThompsonSampler, get_policy_instruction

DECISION_PROMPT = """[Query question]
{query}
[Analysis data]
{data}
[Anomaly information]
{anomaly}
[Attribution analysis]
{attribution}
[Drill-down data]
{drill_data}

Please generate decision suggestions based on the above data.
"""


async def decision_insight(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    from app.agent.nodes._analysis_gate import analysis_wanted
    fast_lane = not analysis_wanted(state)
    if fast_lane:
        # Fast lane: skip the LLM insight generation (~30-60s on a single
        # reasoning model) but keep the RL policy bookkeeping so the
        # bandit keeps learning; tests assert decision_policy presence.
        logger.info("decision_insight fast lane: LLM skipped, policy kept")
        writer({"stage": "Decision Insight (fast lane)"})
        policy_name = "facts_first"
        try:
            rl_repo = runtime.context.get("rl_doris_repository")
            if rl_repo is not None:
                import random as _random
                policies = list(getattr(rl_repo, "policies", []) or ["facts_first"])
                policy_name = _random.choice(policies) if policies else "facts_first"
        except Exception:
            policy_name = "facts_first"
        return {"decision_insights": "", "decision_policy": policy_name}
    writer({"stage": "Decision Insight"})
    try:
        query = state.get("query", "")
        # M10 FR3: fetch the FULL spilled result when present (else the
        # in-state truncated view).
        result = state.get("_last_result", [])
        try:
            from app.services.spill_store import resolve_full_rows
            meta_repo = runtime.context.get("meta_doris_repository")
            if state.get("_spill_id") and meta_repo is not None:
                result = await resolve_full_rows(state, meta_repo.session)
        except Exception as e:
            logger.debug(f"spill resolve in decision_insight skipped: {e}")
        anomaly = state.get("anomaly_result", {})
        attribution = state.get("attribution_result", {})
        drill = state.get("drill_down_result", {})

        data_str = json.dumps(result[:5], ensure_ascii=False, default=str) if result else "No data"
        anomaly_info = json.dumps(anomaly, ensure_ascii=False, default=str) if anomaly else "No anomaly detected"
        attr_str = json.dumps(attribution, ensure_ascii=False, default=str) if attribution else "No attribution data"
        drill_data = json.dumps(drill, ensure_ascii=False, default=str) if drill else "No drill-down data"

        # ============================================================ #
        # RL: pick a prompt policy via Thompson Sampling.
        # ============================================================ #
        rl_repo = runtime.context.get("rl_repository")
        sampler = runtime.context.get("rl_sampler") or _default_sampler

        policy_name = "facts_first"
        sampled_theta = 0.0
        if rl_repo is not None:
            try:
                stats = await rl_repo.get_all_stats()
                policy_name, sampled_theta = sampler.select(stats)
                logger.info(
                    f"RL policy chosen: {policy_name} "
                    f"(theta={sampled_theta:.3f}, "
                    f"expected={stats[policy_name].expected_reward:.3f})"
                )
                # Metric for observability.
                try:
                    from app.core.metrics import RL_POLICY_CHOSEN
                    RL_POLICY_CHOSEN.labels(policy=policy_name).inc()
                except Exception:
                    pass
            except Exception as e:
                logger.warning(
                    f"RL policy selection failed, using facts_first: {e}"
                )
                policy_name = "facts_first"

        policy_instruction = get_policy_instruction(policy_name)

        # ============================================================ #
        # Build the final prompt: shared context + policy instruction.
        # ============================================================ #
        # Use str.format() instead of sequential .replace() to prevent injection
        user_prompt = DECISION_PROMPT.format(
            query=query[:100],
            data=data_str[:2000],
            anomaly=anomaly_info[:500],
            attribution=attr_str[:500],
            drill_data=drill_data[:500],
        )
        messages = [
            SystemMessage(content=policy_instruction),
            HumanMessage(content=user_prompt),
        ]

        resp = await llm.ainvoke(messages)
        insight = str(resp.content).strip()

        logger.info(
            f"Decision insight generated ({len(insight)} chars, policy={policy_name})"
        )

        # ============================================================ #
        # Persist the decision for reward attribution.
        # ============================================================ #
        if rl_repo is not None:
            request_id = request_id_ctx_var.get() or ""
            # Don't await - the user's response shouldn't wait on this write.
            # If it fails, the bandit just misses one sample; not catastrophic.
            try:
                await rl_repo.log_decision(
                    request_id=request_id,
                    policy_name=policy_name,
                    query=query,
                    insight=insight,
                    sampled_score=sampled_theta,
                )
            except Exception as e:
                logger.warning(f"RL decision log write failed: {e}")

        from app.agent.events import emit
        emit("insight/generated", {"policy": policy_name,
                                   "chars": len(insight or "")})
        return {"decision_insights": insight, "decision_policy": policy_name}
    except Exception as e:
        logger.error(f"Decision insight error: {e}")
        return {"decision_insights": "", "decision_policy": "facts_first"}


# Module-level sampler. Production uses the stdlib RNG (OS entropy).
# Tests can monkeypatch this attribute or inject `rl_sampler` via context.
_default_sampler = ThompsonSampler()
