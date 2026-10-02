"""Token meter + injection budget (PRD M8).

dsh insight: token-meter measures context pressure from the session
replay; compaction triggers on it. AskInsight mapping for the P0 seam:

- estimate(text): cheap heuristic used BEFORE the call. CJK chars count
  ~1 token each; ASCII ~4 chars/token (OpenAI-ish), tuned by a global
  calibration ratio that calibrate() updates from real LLM usage
  (FR1: LLM 响应后用 usage 真值校准).
- budget_parts(): applies the injection budget to the generate_sql
  prompt parts (FR2: DDL <=60% / few-shot <=15% / history <=15% /
  glossary+dimension <=10%, configurable). Over-budget trimming follows
  the PRD priority: plan_hint > dimension > glossary > DDL 列细节 >
  few-shot > history (never trims the query itself).
- Pressure + trim details land as a context/pressure session event
  (FR3) and Prometheus metrics (FR4).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from app.core.log import logger

# Calibration: estimated_tokens * ratio ~= real tokens (updated by usage).
_CALIB_RATIO_MIN, _CALIB_RATIO_MAX = 0.5, 2.0
_calibration_ratio = 1.0

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def estimate_tokens(text: str) -> int:
    """Heuristic token estimate: CJK ~1/char, ASCII ~1 per 4 chars."""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    ascii_len = max(0, len(text) - cjk)
    return int((cjk + ascii_len / 4.0) * _calibration_ratio)


def calibrate(estimated: int, actual_usage: int) -> None:
    """Update the global calibration ratio from a real usage datapoint
    (FR1). Clamped + best-effort; never raises."""
    global _calibration_ratio
    try:
        if estimated > 0 and actual_usage > 0:
            r = actual_usage / estimated
            r = max(_CALIB_RATIO_MIN, min(_CALIB_RATIO_MAX, r))
            # Exponential smoothing keeps single outliers tame.
            _calibration_ratio = round(
                0.8 * _calibration_ratio + 0.2 * r, 4)
    except Exception:
        pass


def calibration_ratio() -> float:
    return _calibration_ratio


@dataclass
class BudgetConfig:
    total: int = int(os.getenv(
        "LLM_PROMPT_TOKEN_BUDGET", "16000"))
    shares: dict = field(default_factory=lambda: {
        "ddl": 0.60,          # DDL + metrics
        "few_shot": 0.15,     # feedback examples
        "history": 0.15,      # dialogue history
        "glossary": 0.10,     # dimension + glossary hints
    })


# Trim priority (FR2): keep earlier parts longer. `query` is never cut.
_PART_PRIORITY = ["plan_hint", "dimension", "glossary",
                  "ddl_detail", "few_shot", "history"]


@dataclass
class TrimRecord:
    part: str
    before: int
    after: int


def budget_parts(parts: dict, cfg: BudgetConfig = None) -> tuple[dict, list[TrimRecord]]:
    """Apply per-part token budgets. `parts` maps names to strings; the
    names follow _PART_PRIORITY conventions plus 'query' (untouchable)
    and arbitrary extras (bounded by remaining budget, priority tail).

    Returns (bounded_parts, trims)."""
    cfg = cfg or BudgetConfig()
    trims: list[TrimRecord] = []
    bounded = dict(parts)

    def _cap(part_key: str, token_limit: int) -> None:
        cur = bounded.get(part_key)
        if not cur:
            return
        est = estimate_tokens(cur)
        if est <= token_limit:
            return
        # Cut by characters proportionally (heuristic).
        keep_chars = max(0, int(len(cur) * (token_limit / max(est, 1))))
        bounded[part_key] = cur[:keep_chars]
        trims.append(TrimRecord(part_key, est, token_limit))

    # Map concrete part names onto budget buckets.
    ddl_tokens = estimate_tokens(bounded.get("ddl", "") or "")
    metrics_tokens = estimate_tokens(bounded.get("metrics", "") or "")
    ddl_budget = max(0, int(cfg.total * cfg.shares["ddl"]) - metrics_tokens)
    if ddl_tokens > ddl_budget:
        _cap("ddl", ddl_budget)

    _cap("few_shot", int(cfg.total * cfg.shares["few_shot"]))
    _cap("history", int(cfg.total * cfg.shares["history"]))
    glossary_budget = int(cfg.total * cfg.shares["glossary"])
    for key in ("dimension", "glossary"):
        _cap(key, max(0, glossary_budget // 2))
    # plan_hint sits in the query prefix - bounded together with extras.
    _cap("plan_hint", cfg.total)
    return bounded, trims


def record_pressure(node: str, parts: dict, bounded: dict,
                    trims: list[TrimRecord]) -> dict:
    """context/pressure payload (FR3) + Prometheus metrics (FR4)."""
    estimated = {k: estimate_tokens(v or "") for k, v in parts.items()}
    bounded_total = {k: estimate_tokens(v or "") for k, v in bounded.items()}
    payload = {
        "node": node,
        "estimated": estimated,
        "total_estimated": sum(estimated.values()),
        "total_bounded": sum(bounded_total.values()),
        "trimmed": [{"part": t.part, "before": t.before, "after": t.after}
                    for t in trims],
    }
    try:
        from app.core.metrics import (
            CONTEXT_TOKENS_ESTIMATED, CONTEXT_TRIMMED_PARTS,
        )
        CONTEXT_TOKENS_ESTIMATED.observe(payload["total_estimated"])
        if trims:
            CONTEXT_TRIMMED_PARTS.inc(len(trims))
    except Exception as e:
        logger.debug(f"context pressure metrics failed: {e}")
    try:
        from app.agent.events import emit
        emit("context/pressure", payload)
    except Exception:
        pass
    return payload
