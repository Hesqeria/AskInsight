
# --------------------------------------------------------------------------- #
# Fast-lane gate: lookups skip the heavy analysis tail entirely.
# The LLM analysis tail (insight + python) costs ~25s per invocation, so it
# runs ONLY on explicit analytical intent (为什么/分析/对比/趋势/...).
# Row-count auto-trigger was removed - the rule-based summary/delta/chips
# already interpret plain results. Set ANALYSIS_MIN_ROWS=N (e.g. 3) to
# restore the old auto-trigger for multi-row results.
# --------------------------------------------------------------------------- #
_ANALYSIS_INTENT_WORDS = [
    "为什么", "为何", "怎么", "如何", "分析", "解读", "洞察", "异常", "归因",
    "对比", "原因", "建议", "趋势", "走势", "波动", "变化", "分布",
    "why", "how", "analyze", "insight", "compare", "trend",
]
FAST_MIN_ROWS = int(__import__("os").getenv("ANALYSIS_MIN_ROWS", "0"))


def analysis_wanted(state) -> bool:
    """True when the analysis tail (insight/python/drill) adds value."""
    q = (state.get("query") or "").lower()
    if any(w in q for w in _ANALYSIS_INTENT_WORDS):
        return True
    if FAST_MIN_ROWS > 0:
        result = state.get("_last_result") or []
        return len(result) >= FAST_MIN_ROWS
    return False
