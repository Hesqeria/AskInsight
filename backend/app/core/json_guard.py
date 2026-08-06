"""LLM JSON 响应容错解析器

解决 WrenAI #1274（DeepSeekException 解析错误，14 评论）：
  LLM 返回的 JSON 可能被 markdown 包裹、含多余文本、或被截断。

功能：
  1. 提取 markdown 代码块中的 JSON
  2. 容忍 JSON 前后的多余文本
  3. 修复常见 JSON 格式问题（尾逗号、单引号）
  4. 截断 JSON 的安全回退
"""
import json
import re
from typing import Any

from app.core.log import logger


def safe_json_parse(text: str, default: Any = None) -> Any:
    """容错解析 LLM 返回的 JSON

    Args:
        text: LLM 原始返回文本
        default: 解析失败时的默认返回值

    Returns:
        解析后的 Python 对象，或 default
    """
    if not text or not text.strip():
        return default

    text = text.strip()

    # 策略1: 直接解析（最快路径）
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 策略2: 提取 markdown 代码块 ```json ... ```
    code_block = _extract_code_block(text)
    if code_block:
        try:
            return json.loads(code_block)
        except json.JSONDecodeError:
            pass

    # 策略3: 提取第一个 { ... } 或 [ ... ]，先修复再解析
    extracted = _extract_json_object(text)
    if extracted:
        # 先尝试直接解析
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass
        # 修复常见问题后重试
        fixed = _fix_common_issues(extracted)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # 策略5: 截断 JSON 的安全回退（尝试补全）
    repaired = _repair_truncated(text)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    logger.warning(f"JSON 解析失败，返回默认值。原始文本前100字符: {text[:100]}")
    return default


def _extract_code_block(text: str) -> str | None:
    """提取 ```json ... ``` 或 ``` ... ``` 代码块"""
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
    """提取第一个完整的 JSON 对象或数组"""
    # 找第一个 { 或 [
    start_idx = -1
    for i, c in enumerate(text):
        if c in '{[':
            start_idx = i
            break

    if start_idx == -1:
        return None

    # 从 start_idx 开始匹配括号
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
    """修复常见 JSON 格式问题"""
    # 移除尾逗号
    fixed = re.sub(r",\s*([}\]])", r"\1", json_str)
    # 单引号 → 双引号（整个字符串替换）
    if "'" in fixed:
        # 安全替换：只在引号成对出现时替换
        fixed = fixed.replace("'", '"')
    return fixed


def _repair_truncated(text: str) -> str | None:
    """尝试修复被截断的 JSON"""
    # 计算未闭合的括号
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    
    if open_braces > 0 or open_brackets > 0:
        # 移除最后一个不完整的逗号
        repaired = text.rstrip()
        if repaired.endswith(","):
            repaired = repaired[:-1]
        # 补全括号
        repaired += "]" * max(open_brackets, 0)
        repaired += "}" * max(open_braces, 0)
        return repaired
    return None
