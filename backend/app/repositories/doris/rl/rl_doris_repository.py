"""RL (Thompson Sampling) repository.

Persists policy posteriors and decision logs to Doris. All methods are
defensive: they swallow DB errors and return sensible defaults so a
missing/broken `rl_*` table never breaks the decision_insight pipeline.
The caller is expected to have run the DDL in conf/ddl/rl_schema.sql.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import logger
from app.rl.bandit import (
    PolicyStats, ACTIVE_POLICIES, DEFAULT_ALPHA, DEFAULT_BETA,
)


class RlDorisRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------ #
    # Policy stats (posterior snapshot)
    # ------------------------------------------------------------------ #
    async def get_all_stats(self) -> dict[str, PolicyStats]:
        """Load all policy stats. Missing policies are filled in with the
        default Beta(1,1) prior so the bandit still explores them."""
        result = {}
        # Pre-populate with priors so newly-added policies are explored
        # even before the operator seeds the table.
        for name in ACTIVE_POLICIES:
            result[name] = PolicyStats(policy_name=name)
        try:
            rows = await self.session.execute(text(
                "SELECT policy_name, alpha, beta, total_calls, "
                "positive_rewards, negative_rewards FROM data_agent.rl_policy_stats"
            ))
            for r in rows.fetchall():
                name = r[0]
                result[name] = PolicyStats(
                    policy_name=name,
                    alpha=float(r[1] or DEFAULT_ALPHA),
                    beta=float(r[2] or DEFAULT_BETA),
                    total_calls=int(r[3] or 0),
                    positive_rewards=int(r[4] or 0),
                    negative_rewards=int(r[5] or 0),
                )
        except Exception as e:
            logger.warning(
                f"RL stats load failed, using Beta(1,1) priors for all "
                f"policies: {e}"
            )
        return result

    async def upsert_stats(self, stats: PolicyStats) -> None:
        """Persist a policy's updated posterior. Uses INSERT-on-duplicate
        semantics; Doris supports `INSERT INTO ... ON DUPLICATE KEY` for
        UNIQUE/PRIMARY tables but for max compatibility we do
        delete-then-insert inside a single tx."""
        try:
            await self.session.execute(
                text("DELETE FROM data_agent.rl_policy_stats WHERE policy_name = :n"),
                {"n": stats.policy_name},
            )
            await self.session.execute(text("""
                INSERT INTO data_agent.rl_policy_stats
                    (policy_name, alpha, beta, total_calls,
                     positive_rewards, negative_rewards, last_updated)
                VALUES (:n, :a, :b, :tc, :pr, :nr, :ts)
            """), {
                "n": stats.policy_name,
                "a": stats.alpha,
                "b": stats.beta,
                "tc": stats.total_calls,
                "pr": stats.positive_rewards,
                "nr": stats.negative_rewards,
                "ts": datetime.now(),
            })
            await self.session.commit()
        except Exception as e:
            logger.warning(f"RL stats upsert failed for {stats.policy_name}: {e}")
            # Best-effort rollback so the session stays usable.
            try:
                await self.session.rollback()
            except Exception:
                pass

    async def reset_all_stats(self) -> int:
        """Reset every policy to Beta(1,1). Returns the number of rows
        affected (for the admin endpoint)."""
        try:
            # Wipe then re-seed with priors for all active policies.
            await self.session.execute(text("DELETE FROM data_agent.rl_policy_stats"))
            for name in ACTIVE_POLICIES:
                await self.session.execute(text("""
                    INSERT INTO data_agent.rl_policy_stats
                        (policy_name, alpha, beta, total_calls,
                         positive_rewards, negative_rewards, last_updated)
                    VALUES (:n, 1.0, 1.0, 0, 0, 0, :ts)
                """), {"n": name, "ts": datetime.now()})
            await self.session.commit()
            return len(ACTIVE_POLICIES)
        except Exception as e:
            logger.warning(f"RL stats reset failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
            return 0

    # ------------------------------------------------------------------ #
    # Decision log (for reward attribution + DPO data export)
    # ------------------------------------------------------------------ #
    async def log_decision(
        self,
        request_id: str,
        policy_name: str,
        query: str,
        insight: str,
        sampled_score: float,
    ) -> None:
        """Record that policy `policy_name` was used to generate `insight`
        for `request_id`. This row is later joined with the user's quality
        rating to close the RL feedback loop."""
        try:
            await self.session.execute(text("""
                INSERT INTO data_agent.rl_decision_log
                    (id, request_id, policy_name, query, sampled_score,
                     insight, created_at)
                VALUES (:id, :rid, :p, :q, :ss, :ins, :ts)
            """), {
                "id": str(uuid.uuid4()),
                "rid": request_id,
                "p": policy_name,
                "q": query[:500],
                "ss": float(sampled_score),
                "ins": insight,
                "ts": datetime.now(),
            })
            await self.session.commit()
        except Exception as e:
            logger.warning(f"RL decision log write failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass

    async def get_decision_by_request(
        self, request_id: str,
    ) -> Optional[dict]:
        """Look up the policy that produced the insight for a given
        request_id. Used by the reward endpoint."""
        try:
            rows = await self.session.execute(text("""
                SELECT policy_name, query, insight FROM data_agent.rl_decision_log
                WHERE request_id = :rid
                ORDER BY created_at DESC LIMIT 1
            """), {"rid": request_id})
            r = rows.fetchone()
            if not r:
                return None
            return {"policy_name": r[0], "query": r[1], "insight": r[2]}
        except Exception as e:
            logger.warning(f"RL decision lookup failed: {e}")
            return None

    async def list_decisions(self, limit: int = 100) -> list[dict]:
        """Recent decision log entries (admin / observability)."""
        try:
            rows = await self.session.execute(text("""
                SELECT request_id, policy_name, sampled_score, query,
                       created_at
                FROM data_agent.rl_decision_log
                ORDER BY created_at DESC LIMIT :n
            """), {"n": limit})
            return [
                {
                    "request_id": r[0],
                    "policy_name": r[1],
                    "sampled_score": float(r[2] or 0.0),
                    "query": r[3],
                    "created_at": str(r[4]),
                }
                for r in rows.fetchall()
            ]
        except Exception as e:
            logger.warning(f"RL decisions list failed: {e}")
            return []
