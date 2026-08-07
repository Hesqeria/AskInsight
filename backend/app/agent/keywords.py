"""Unified keyword sanitization for all recall/matching nodes."""

MAX_KEYWORD_LENGTH = 100


def sanitize_keyword(kw: str) -> str:
    """Clean a single keyword: truncate, strip injection chars."""
    s = str(kw).strip()
    # Remove control chars and SQL/LLM special chars
    for ch in "%;\"'`\\n\r\t":
        s = s.replace(ch, "")
    return s[:MAX_KEYWORD_LENGTH]


def sanitize_keywords(keywords: list) -> list:
    """Clean and deduplicate a keyword list."""
    seen = set()
    result = []
    for kw in keywords:
        clean = sanitize_keyword(kw)
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def expand_and_clean(keywords: list, llm_result) -> list:
    """Merge user keywords with LLM-expanded keywords and sanitize."""
    all_kw = list(keywords)
    try:
        expanded = list(llm_result) if isinstance(llm_result, (list, set)) else []
        all_kw.extend(str(k) for k in expanded if str(k).strip())
    except Exception:
        pass
    return sanitize_keywords(all_kw)
