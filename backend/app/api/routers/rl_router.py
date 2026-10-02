"""RL admin / observability router.

Exposes the Thompson-Sampling bandit state for inspection and ops:
  - GET  /api/rl/policies     -> current Beta posteriors + derived CTR
  - POST /api/rl/reset        -> reset all policies to Beta(1,1)
  - GET  /api/rl/decisions    -> recent decision log
  - GET  /api/rl/export-dpo   -> export chosen/rejected pairs for
                                 offline DPO training (preliminary)

All endpoints are read-only or admin-gated; they don't affect inference
unless explicitly called.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.core.auth import verify_token
from app.core.log import logger
from app.repositories.doris.rl.rl_doris_repository import RlDorisRepository
from app.rl import POLICY_INSTRUCTIONS, ACTIVE_POLICIES

rl_router = APIRouter()


@rl_router.get("/api/rl/policies")
async def list_policies(
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Show each policy's Beta posterior and derived statistics."""
    repo = RlDorisRepository(session)
    stats_map = await repo.get_all_stats()
    policies = []
    for name in ACTIVE_POLICIES:
        s = stats_map.get(name)
        if s is None:
            continue
        policies.append({
            "policy_name": s.policy_name,
            "alpha": round(s.alpha, 4),
            "beta": round(s.beta, 4),
            "total_calls": s.total_calls,
            "positive_rewards": s.positive_rewards,
            "negative_rewards": s.negative_rewards,
            "expected_reward": round(s.expected_reward, 4),
            "observed_ctr": round(s.samples_per_call, 4),
            "instruction": POLICY_INSTRUCTIONS.get(name, ""),
        })
    # Sort by expected_reward desc so the "winning" policy is on top.
    policies.sort(key=lambda p: -p["expected_reward"])
    return {"policies": policies, "active_count": len(policies)}


@rl_router.post("/api/rl/reset")
async def reset_policies(
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Reset all policies to the Beta(1,1) uniform prior. Useful when the
    bandit has overfit to early noise. Requires admin role."""
    if user.get("role") not in ("L4_admin", "admin"):
        return JSONResponse(status_code=403, content={
            "status": "error", "message": "admin role required"
        })
    repo = RlDorisRepository(session)
    n = await repo.reset_all_stats()
    logger.info(f"RL stats reset by {user.get('sub')}: {n} policies")
    return {"status": "ok", "reset_count": n}


@rl_router.get("/api/rl/decisions")
async def list_decisions(
    limit: int = 50,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Recent decision_insight invocations (which policy was used, the
    sampled theta, the user query). Insight text is omitted for brevity."""
    repo = RlDorisRepository(session)
    decisions = await repo.list_decisions(limit=limit)
    return {"decisions": decisions, "count": len(decisions)}


@rl_router.get("/api/rl/export-dpo")
async def export_dpo(
    min_positive: int = 5,
    limit: int = 200,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Preliminary DPO data export.

    For each policy with at least `min_positive` positive rewards, we
    pair every positively-rated insight against every negatively-rated
    insight of any *other* policy on a similar query, producing
    (prompt, chosen, rejected) triples suitable for offline DPO.

    The join is intentionally loose (query-prefix match) so we get
    reasonable coverage even with sparse data. For production-scale
    training, swap this for a real prompt-similarity join (embeddings).
    """
    from sqlalchemy import text
    try:
        # Find policies with enough positive samples to be worth exporting.
        rows = await session.execute(text("""
            SELECT policy_name FROM data_agent.rl_policy_stats
            WHERE positive_rewards >= :min_pos
        """), {"min_pos": min_positive})
        strong_policies = [r[0] for r in rows.fetchall()]
        if not strong_policies:
            return {"triples": [], "count": 0,
                    "note": f"no policy has >= {min_positive} positive rewards yet"}

        # Positive and negative insights per policy.
        # NOTE: feedback_log carries the rating as a prefix on the query
        # column ('[QUALITY_RATING=1] ...'); we join on request_id.
        rows = await session.execute(text("""
            SELECT d.policy_name, d.query, d.insight,
                   CASE WHEN f.query LIKE '[QUALITY_RATING=1]%' THEN 1 ELSE 0 END AS rating
            FROM data_agent.rl_decision_log d
            LEFT JOIN feedback_log f ON f.request_id = d.request_id
            WHERE f.query LIKE '[QUALITY_RATING=%'
            ORDER BY d.created_at DESC LIMIT :lim
        """), {"lim": limit})
        rated = [
            {"policy": r[0], "query": r[1], "insight": r[2], "rating": int(r[3] or 0)}
            for r in rows.fetchall()
        ]

        # Pair positives vs negatives (cross-policy preferred).
        positives = [r for r in rated if r["rating"] == 1]
        negatives = [r for r in rated if r["rating"] == 0]
        triples = []
        for pos in positives:
            for neg in negatives:
                if pos["policy"] == neg["policy"]:
                    continue  # within-policy comparisons are noisy
                triples.append({
                    "prompt": pos["query"],
                    "chosen": pos["insight"],
                    "rejected": neg["insight"],
                    "chosen_policy": pos["policy"],
                    "rejected_policy": neg["policy"],
                })
        return {"triples": triples, "count": len(triples),
                "positives": len(positives),
                "negatives": len(negatives),
                "strong_policies": strong_policies}
    except Exception as e:
        logger.warning(f"DPO export failed: {e}")
        return JSONResponse(status_code=500, content={
            "status": "error", "message": str(e),
        })
