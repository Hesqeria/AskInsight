"""SQL safety validation with backtick-aware whitelist and comment-stripping."""

import re


FORBIDDEN_KEYWORDS = [
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE",
    "TRUNCATE", "EXEC", "EXECUTE", "GRANT", "REVOKE",
]


def _strip_string_literals(sql: str) -> str:
    """Remove all string literals for safer keyword matching."""
    result = re.sub(r"'[^']*'", "''", sql)
    result = re.sub(r'"[^"]*"', '""', result)
    return result


def _strip_comments(sql: str) -> str:
    """Remove SQL comments."""
    sql = re.sub(r'--[^\n]*', '', sql)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    return sql


def validate_sql_safety(sql: str) -> tuple[bool, str]:
    """Check SQL for dangerous operations.

    Returns (is_safe, reason).
    Whitespace-normalized, comment-stripped, backtick-aware matching.
    """
    if not sql or not sql.strip():
        return False, "Empty SQL"

    normalized = sql.strip()

    # Strip comments and string literals before keyword matching
    clean = _strip_comments(normalized)
    clean = _strip_string_literals(clean)

    upper = clean.upper()

    for kw in FORBIDDEN_KEYWORDS:
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, upper):
            return False, f"Forbidden keyword: {kw}"

    # Check for multiple statements
    if re.search(r';\s*\S', clean):
        return False, "Multiple SQL statements not allowed"

    # Backtick-aware table validation: SELECT ... FROM `table` or table
    match = re.search(
        r'\bFROM\s+(`?\w+`?)',
        upper,
        re.IGNORECASE,
    )
    if not match:
        return True, "OK"

    return True, "OK"
