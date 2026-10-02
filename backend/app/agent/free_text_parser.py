"""Parse free-text clarification responses into structured selections.

When a user types "其实是想看 7 月份的华北订单" instead of picking
buttons, we can't directly map it to plan fields. This module uses the
LLM (with the existing ontology registries as context) to extract:

    selections = {
        "measure": "GMV",
        "time": "上月",          # passed through parse_time_expression
        "group_by": ["C050"],    # dimension class IDs
        "dimension": {...},      # optional dimension filter
    }

Fallback: if the LLM returns garbage or the call fails, we return an
empty selections dict and the caller degrades to button re-prompt.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Allowed measure / dimension / group_by keys the LLM can emit.
_ALLOWED_KEYS = {"measure", "time", "group_by", "dimension"}


_PARSE_PROMPT = """你是智能问数系统的意图解析器。用户对澄清问题给出了自由文本回复,
你需要把它解析成结构化字段,用于补全一个语义查询计划。

【可选指标】(来自指标注册表)
{measure_options}

【可选时间表达式】
今天/昨天/近N天/本月/上月/本季度 等中文时间短语

【可选分组维度】
C050=按地区, C021=按品类, C011=按客户, C020=按商品

【用户原问题】
{question}

【用户自由文本回复】
{free_text}

请输出 JSON(不要其他文字),格式:
{{
  "measure": "GMV" | "order_count" | "DAU" | null,
  "time": "上月" | "今天" | null,
  "group_by": ["C050", "C011"] | [],
  "dimension": {{"class_id": "C050", "value": "华北"}} | null,
  "explanation": "一句话说明你如此解析的理由"
}}
如果无法从文本中确定某字段,置为 null,不要猜测。"""


def _build_measure_options() -> str:
    """Render available measure terms for the prompt."""
    from app.agent.nodes.semantic_grounding import MEASURE_REGISTRY
    if not MEASURE_REGISTRY:
        return "(无)"
    return "\n".join(f"- {term}: {spec['aggregation']} on {spec['column']}"
                     for term, spec in MEASURE_REGISTRY.items())


def parse_free_text(
    question: str,
    free_text: str,
    llm_client=None,
) -> dict:
    """Parse user's free-text response into a selections dict.

    Args:
        question: original NL question (for context).
        free_text: user's free-form clarification response.
        llm_client: object with `.invoke(prompt)` (LangChain-style) or
            `async .ainvoke()`. If None, falls back to the app LLM.

    Returns:
        dict with optional keys: measure / time / group_by / dimension.
        Empty dict on failure (caller re-prompts with buttons).
    """
    if not free_text or not free_text.strip():
        return {}

    prompt = _PARSE_PROMPT.format(
        measure_options=_build_measure_options(),
        question=question,
        free_text=free_text.strip(),
    )

    client = llm_client or _default_llm()
    if client is None:
        logger.warning("parse_free_text: no LLM client available")
        return {}

    try:
        raw = client.invoke(prompt)
        # Handle both str and AIMessage returns.
        text = raw.content if hasattr(raw, "content") else str(raw)
        parsed = _extract_json(text)
        if not parsed:
            return {}
        return _sanitize_selections(parsed)
    except Exception as e:
        logger.warning(f"parse_free_text failed: {e}")
        return {}


async def aparse_free_text(
    question: str,
    free_text: str,
    llm_client=None,
) -> dict:
    """Async version for use inside the FastAPI resume endpoint."""
    if not free_text or not free_text.strip():
        return {}

    prompt = _PARSE_PROMPT.format(
        measure_options=_build_measure_options(),
        question=question,
        free_text=free_text.strip(),
    )

    client = llm_client or _default_llm()
    if client is None:
        logger.warning("aparse_free_text: no LLM client available")
        return {}

    try:
        raw = await client.ainvoke(prompt)
        text = raw.content if hasattr(raw, "content") else str(raw)
        parsed = _extract_json(text)
        if not parsed:
            return {}
        return _sanitize_selections(parsed)
    except Exception as e:
        logger.warning(f"aparse_free_text failed: {e}")
        return {}


def _default_llm():
    try:
        from app.agent.llm import llm
        return llm
    except Exception as e:
        logger.debug(f"no default llm: {e}")
        return None


def _extract_json(text: str) -> Optional[dict]:
    """Extract a JSON object from LLM output (may be wrapped in markdown
    code fences or have trailing prose)."""
    if not text:
        return None
    # Try direct parse first.
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    # Strip markdown fences.
    import re
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (ValueError, TypeError):
            pass
    # Find the first {...} block.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (ValueError, TypeError):
            pass
    return None


def _sanitize_selections(parsed: dict) -> dict:
    """Validate + normalize the parsed JSON to allowed keys only."""
    out: dict = {}
    if not isinstance(parsed, dict):
        return {}

    # measure: must be a known registry term.
    from app.agent.nodes.semantic_grounding import MEASURE_REGISTRY
    if parsed.get("measure") in MEASURE_REGISTRY:
        out["measure"] = parsed["measure"]

    # time: must be a non-empty string (validated later by
    # parse_time_expression during merge).
    if isinstance(parsed.get("time"), str) and parsed["time"].strip():
        out["time"] = parsed["time"].strip()

    # group_by: list of dimension class IDs we know about.
    gb = parsed.get("group_by")
    if isinstance(gb, list):
        known = _known_group_by_classes()
        out["group_by"] = [c for c in gb if c in known]
    elif isinstance(gb, str) and gb:
        if gb in _known_group_by_classes():
            out["group_by"] = [gb]

    # dimension: {class_id, value, ...}.
    dim = parsed.get("dimension")
    if isinstance(dim, dict) and dim.get("class_id") and dim.get("value"):
        out["dimension"] = {
            "class_id": str(dim["class_id"]),
            "value": str(dim["value"]),
            "operator": "=",
        }

    return out


def _known_group_by_classes() -> set[str]:
    """Dimension class IDs usable for group_by (those with a value column
    registry entry)."""
    from app.agent.nodes.semantic_grounding import DIM_VALUE_COLUMN_REGISTRY
    return set(DIM_VALUE_COLUMN_REGISTRY.keys())
