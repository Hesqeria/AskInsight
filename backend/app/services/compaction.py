"""Session history compaction (PRD M4).

dsh compaction three rules, adapted to AskInsight's plain-string
history:
1. 免模型修剪先行 (model-free prune first): old turns keep only a
   short head of each entry - no LLM call.
2. LLM 摘要 as the second stage: if the pruned view still exceeds the
   budget, the oldest half is summarized into one line. Both stages run
   inside a compaction/start ... compaction/end event pair so a crash
   mid-compaction is detectable (start without end = leftover marker).
3. resolve_context consumes the COMPACTED view (M4 FR4) instead of a
   blind last-3 truncation.

History entries may be plain strings (legacy) or compacted summaries -
both flow through unchanged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.core.log import logger
from app.core.token_meter import estimate_tokens

# Trigger: history tokens >= this fraction of the prompt budget (FR1).
COMPACT_THRESHOLD_RATIO = float(os.getenv("COMPACTION_THRESHOLD", "0.8"))
# Model-free prune: keep this many chars per old entry.
PRUNE_KEEP_CHARS = 80


@dataclass
class CompactionResult:
    history: list = field(default_factory=list)
    pruned: int = 0
    summarized: int = 0
    used_llm: bool = False


def _needs_compaction(history_tokens: int, budget: int) -> bool:
    return history_tokens >= int(budget * COMPACT_THRESHOLD_RATIO)


def _prune_old(history: list, keep_recent: int = 2) -> tuple[list, int]:
    """Model-free: entries beyond the most recent `keep_recent` are cut
    to PRUNE_KEEP_CHARS (head keeps measure + dimension info)."""
    if len(history) <= keep_recent:
        return list(history), 0
    out = [str(h)[:PRUNE_KEEP_CHARS] for h in history[:-keep_recent]]
    out.extend(str(h) for h in history[-keep_recent:])
    return out, len(history) - keep_recent


async def _llm_summarize(entries: list) -> str:
    """Summarize the oldest entries into ONE compact line (FR3)."""
    from langchain_core.messages import HumanMessage
    from app.agent.llm import llm
    text = "\n".join(f"- {e}" for e in entries)
    prompt = (
        "将以下历史数据查询问题压缩为一段不超过 120 字的摘要,保留指标、"
        "维度、时间范围与结论要点,不要解释:\n" + text[:6000]
    )
    resp = await llm.ainvoke([HumanMessage(content=prompt)])
    summary = str(resp.content).strip().replace("\n", " ")[:200]
    return f"[历史摘要] {summary}"


def _emit(type_: str, payload: dict) -> None:
    try:
        from app.agent.events import emit
        emit(type_, payload)
    except Exception:
        pass


async def maybe_compact(history: list, budget: int = None) -> CompactionResult:
    """Prune, then summarize if still over budget. Emits the
    compaction/start|summary|end event transaction (FR3)."""
    if not history:
        return CompactionResult(history=[])
    if budget is None:
        try:
            from app.core.token_meter import BudgetConfig
            budget = int(BudgetConfig().total * 0.15)  # history share
        except Exception:
            budget = 2400
    tokens = estimate_tokens("\n".join(str(h) for h in history))
    if not _needs_compaction(tokens, budget):
        return CompactionResult(history=list(history))

    _emit("compaction/start", {"history_tokens": tokens,
                               "budget": budget, "entries": len(history)})
    result = CompactionResult()
    try:
        # Stage 1: model-free prune.
        pruned, count = _prune_old(history)
        result.history = pruned
        result.pruned = count
        # Stage 2: LLM summary when still over budget.
        pruned_tokens = estimate_tokens("\n".join(pruned))
        if _needs_compaction(pruned_tokens, budget) and len(pruned) > 2:
            half = max(1, len(pruned) // 2)
            try:
                summary = await _llm_summarize(pruned[:half])
                result.history = [summary] + pruned[half:]
                result.summarized = half
                result.used_llm = True
                _emit("compaction/summary", {
                    "summarized_entries": half, "summary_chars": len(summary),
                })
            except Exception as e:
                # LLM down: pruned view is still valid (crash-tolerant).
                logger.warning(f"compaction summary failed (prune-only): {e}")
        _emit("compaction/end", {
            "before_tokens": tokens,
            "after_tokens": estimate_tokens("\n".join(result.history)),
            "pruned": result.pruned, "summarized": result.summarized,
            "used_llm": result.used_llm,
        })
        return result
    except Exception as e:
        # No end event -> detectable leftover transaction (dsh rule).
        logger.warning(f"compaction aborted mid-transaction: {e}")
        raise
