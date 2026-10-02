"""Clarify feedback collector (P3-CLARIFY-15).

Persists each resolved clarification session as a training sample for
RL / DPO fine-tuning. A sample captures:
    original question + missing fields
    user's answer (selections / free_text)
    merged final plan + confidence delta
    rounds + outcome

Consumers (downstream training pipelines) can query this table to build
(supervised) demonstration pairs or (preference) pairs:

  - Supervised:  (vague_question, user_selections) → final_plan
  - Preference:  (question, initial_plan, final_plan) where final
                 confidence > initial → the "good" completion

All methods are defensive (return 0/None on DB errors) so a missing
table never breaks the clarify resume path.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import logger


class ClarifyFeedbackRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        clarify_id: str,
        question: str = "",
        initial_confidence: Optional[float] = None,
        missing_fields: Optional[list] = None,
        user_response: Optional[dict] = None,
        final_plan: Optional[dict] = None,
        final_confidence: Optional[float] = None,
        rounds: int = 1,
        outcome: str = "confirmed",
        username: str = "",
    ) -> bool:
        """Insert one feedback/training sample. Returns True on success."""
        try:
            await self.session.execute(text("""
                INSERT INTO data_agent.clarify_feedback
                    (clarify_id, question, initial_confidence, missing_fields,
                     user_response, final_plan, final_confidence,
                     rounds, outcome, username, created_at)
                VALUES (:cid, :q, :ic, :mf, :ur, :fp, :fc,
                        :r, :out, :u, :ts)
            """), {
                "cid": clarify_id,
                "q": question or "",
                "ic": initial_confidence,
                "mf": json.dumps(missing_fields or [], ensure_ascii=False),
                "ur": json.dumps(user_response or {}, ensure_ascii=False),
                "fp": json.dumps(final_plan or {}, ensure_ascii=False),
                "fc": final_confidence,
                "r": int(rounds or 1),
                "out": outcome or "confirmed",
                "u": username or "",
                "ts": datetime.now(),
            })
            await self.session.commit()
            return True
        except Exception as e:
            logger.warning(f"clarify_feedback record failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
            return False

    async def list_samples(self, limit: int = 100) -> list[dict]:
        """Fetch recent training samples (for export / review)."""
        try:
            rows = await self.session.execute(text("""
                SELECT clarify_id, question, initial_confidence,
                       missing_fields, user_response, final_plan,
                       final_confidence, rounds, outcome, username, created_at
                FROM data_agent.clarify_feedback ORDER BY created_at DESC LIMIT :n
            """), {"n": limit})
            out = []
            for r in rows.fetchall():
                out.append({
                    "clarify_id": r[0], "question": r[1] or "",
                    "initial_confidence": r[2],
                    "missing_fields": _loads(r[3], []),
                    "user_response": _loads(r[4], {}),
                    "final_plan": _loads(r[5], {}),
                    "final_confidence": r[6],
                    "rounds": int(r[7] or 1),
                    "outcome": r[8] or "confirmed",
                    "username": r[9] or "",
                    "created_at": str(r[10]) if r[10] else "",
                })
            return out
        except Exception as e:
            logger.warning(f"clarify_feedback list failed: {e}")
            return []

    async def stats(self) -> dict:
        """Aggregate stats for the A/B / quality dashboard.

        Returns per-outcome counts + avg confidence delta.
        """
        try:
            rows = await self.session.execute(text("""
                SELECT outcome, COUNT(*) FROM data_agent.clarify_feedback
                GROUP BY outcome
            """))
            by_outcome = {r[0]: r[1] for r in rows.fetchall()}
        except Exception as e:
            logger.warning(f"clarify_feedback stats failed: {e}")
            by_outcome = {}

        # Avg confidence improvement where we have both values.
        try:
            res = await self.session.execute(text("""
                SELECT AVG(final_confidence - initial_confidence)
                FROM data_agent.clarify_feedback
                WHERE initial_confidence IS NOT NULL
                  AND final_confidence IS NOT NULL
            """))
            row = res.fetchone()
            avg_delta = row[0] if row else 0.0
        except Exception as e:
            logger.warning(f"clarify_feedback delta failed: {e}")
            avg_delta = 0.0

        total = sum(by_outcome.values())
        return {
            "total": total,
            "by_outcome": by_outcome,
            "avg_confidence_delta": round(float(avg_delta or 0.0), 4),
            "confirmed_rate": round(
                by_outcome.get("confirmed", 0) / total, 4) if total else 0.0,
        }


def _loads(v, default):
    if not v:
        return default
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return default
