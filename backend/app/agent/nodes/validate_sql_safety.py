"""SQL safety validation with NFKC normalization, comment stripping, CTE scan."""

import re
import unicodedata


ALLOWED_TABLES = set()  # Populated from meta_config at startup


FORBIDDEN_KEYWORDS = [
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE",
    "TRUNCATE", "EXEC", "EXECUTE", "GRANT", "REVOKE", "MERGE",
]


_HOMOGLYPH_MAP = {
    # Cyrillic -> Latin
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "Х": "X", "а": "a", "е": "e", "о": "o", "р": "p",
    "с": "c", "т": "t", "у": "y", "х": "x",
    # Greek -> Latin
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P",
    "Τ": "T", "Υ": "Y", "Χ": "X",
}


def _normalize(text: str) -> str:
    """NFKC + homoglyph mapping: converts Cyrillic/Greek/fullwidth to ASCII."""
    nfkc = unicodedata.normalize("NFKC", text)
    return "".join(_HOMOGLYPH_MAP.get(c, c) for c in nfkc)


def _strip_string_literals(sql: str) -> str:
    result = re.sub(r"'[^']*'", "''", sql)
    result = re.sub(r'"[^"]*"', '""', result)
    return result


def _strip_comments(sql: str) -> str:
    sql = re.sub(r'--[^\n]*', '', sql)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    sql = re.sub(r'#[^\n]*', '', sql)
    return sql


def validate_sql_safety(sql: str) -> tuple:
    """Check SQL for dangerous operations.

    NFKC-normalizes input to defeat homoglyph attacks,
    strips comments and string literals before keyword matching,
    and scans CTE/WITH bodies for forbidden keywords.
    """
    if not sql or not sql.strip():
        return False, "Empty SQL"

    normalized = _normalize(sql.strip())
    clean = _strip_comments(normalized)
    clean = _strip_string_literals(clean)
    upper = clean.upper()

    for kw in FORBIDDEN_KEYWORDS:
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, upper):
            return False, f"Forbidden keyword: {kw}"

    if re.search(r';\s*[\S\n\r]', clean):
        return False, "Multiple SQL statements not allowed"

    return True, "OK"


def populate_allowed_tables(table_names):
    """Populate ALLOWED_TABLES from meta_config at startup."""
    global ALLOWED_TABLES
    ALLOWED_TABLES = set(t.lower() for t in table_names)
