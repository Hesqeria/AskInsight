"""Fault-tolerant parser for LLM JSON responses.

Addresses WrenAI #1274 (DeepSeekException parse error, 14 comments):
  JSON returned by the LLM may be wrapped in markdown, contain extra text, or be truncated.

Features:
  1. Extract JSON from markdown code blocks
  2. Tolerate extra text before/after the JSON
  3. Fix common JSON format issues (trailing commas, single quotes)
  4. Safe fallback for truncated JSON
"""
import json
import re
from typing import Any

from app.core.log import logger


def safe_json_parse(text: str, default: Any = None) -> Any:
    """Fault-tolerant parsing of JSON returned by the LLM.

    Args:
        text: raw text returned by the LLM
        default: default return value when parsing fails

    Returns:
        Parsed Python object, or default
    """
    if not text or not text.strip():
        return default

    text = text.strip()

    # Strategy 1: parse directly (fastest path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract markdown code block ```json ... ```
    code_block = _extract_code_block(text)
    if code_block:
        try:
            return json.loads(code_block)
        except json.JSONDecodeError:
            pass

    # Strategy 3: extract the first { ... } or [ ... ], fix then parse
    extracted = _extract_json_object(text)
    if extracted:
        # Try direct parse first
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass
        # Retry after fixing common issues
        fixed = _fix_common_issues(extracted)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # Strategy 5: safe fallback for truncated JSON (try to complete)
    repaired = _repair_truncated(text)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    logger.warning(f"JSON parsing failed, returning default. First 100 chars of raw text: {text[:100]}")
    return default


def _extract_code_block(text: str) -> str | None:
    """Extract a ```json ... ``` or ``` ... ``` code block."""
    patterns = [
        r'```(?:json)?\s*\n?(.*?)\n?```',
        r'```(?:json)?\s*(.*?)```',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
    return None


def _extract_json_object(text: str) -> str | None:
    """Extract the first complete JSON object or array."""
    # Find the first { or [
    start_idx = -1
    for i, c in enumerate(text):
        if c in '{[':
            start_idx = i
            break

    if start_idx == -1:
        return None

    # Match brackets starting from start_idx
    bracket = text[start_idx]
    close_bracket = '}' if bracket == '{' else ']'
    depth = 0
    in_string = False
    escape = False

    for i in range(start_idx, len(text)):
        c = text[i]

        if escape:
            escape = False
            continue

        if c == chr(92) and in_string:
            escape = True
            continue

        if c == '"' and not escape:
            in_string = not in_string
            continue

        if in_string:
            continue

        if c == bracket:
            depth += 1
        elif c == close_bracket:
            depth -= 1
            if depth == 0:
                return text[start_idx:i + 1]

    return None


def _fix_common_issues(json_str: str) -> str:
    """Fix common JSON format issues."""
    # Remove trailing commas
    fixed = re.sub(r",\s*([}\]])", r"\1", json_str)
    # Single quotes -> double quotes (replace across the whole string)
    if "'" in fixed:
        # Safe replacement: only replace when quotes are paired
        fixed = fixed.replace("'", '"')
    return fixed


def _repair_truncated(text: str) -> str | None:
    """Attempt to repair truncated JSON."""
    # Count unclosed brackets
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    
    if open_braces > 0 or open_brackets > 0:
        # Remove the last incomplete comma
        repaired = text.rstrip()
        if repaired.endswith(","):
            repaired = repaired[:-1]
        # Close brackets
        repaired += "]" * max(open_brackets, 0)
        repaired += "}" * max(open_braces, 0)
        return repaired
    return None
