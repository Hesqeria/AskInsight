"""Query result formatting: conditional coloring + language adaptation (SQLBot#1213/#1119)."""

POSITIVE_COLOR = "#2ecc71"   # green (positive value / above baseline)
NEGATIVE_COLOR = "#e74c3c"   # red (negative value / below baseline)
WARNING_COLOR = "#f39c12"    # yellow (outliers)
NEUTRAL_COLOR = "#333"       # default color


def format_result_with_color(data: list, baseline_avg: float = None) -> list:
    """Add color markers to numeric results.

    Args:
        data: query results e.g. [{"region": "North", "sales": 80000}]
        baseline_avg: baseline average (used to judge high/low)

    Returns:
        Result with an additional _color field
    """
    if not data:
        return data

    formatted = []
    for row in data:
        new_row = dict(row)
        for key, val in row.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                if baseline_avg and val > 0:
                    ratio = val / baseline_avg if baseline_avg > 0 else 1
                    if ratio >= 1.3:
                        new_row[f"_{key}_color"] = POSITIVE_COLOR
                    elif ratio < 0.7:
                        new_row[f"_{key}_color"] = NEGATIVE_COLOR
                    else:
                        new_row[f"_{key}_color"] = NEUTRAL_COLOR
                elif val < 0:
                    new_row[f"_{key}_color"] = NEGATIVE_COLOR
        formatted.append(new_row)
    return formatted


def detect_language(query: str) -> str:
    """Detect the query language (SQLBot#1119/DB-GPT#3152).

    Returns:
        "zh" (Chinese) | "en" (English) | "mixed"
    """
    if not query:
        return "zh"
    chinese_count = sum(1 for c in query if '\u4e00' <= c <= '\u9fff')
    total_chars = len(query.strip())
    if total_chars == 0:
        return "zh"
    chinese_ratio = chinese_count / total_chars
    if chinese_ratio > 0.3:
        return "zh"
    elif chinese_ratio < 0.05:
        return "en"
    else:
        return "mixed"


def get_column_alias(col_name: str, language: str) -> str:
    """Return column aliases based on language."""
    aliases = {
        "total_amount": {"zh": "Total Sales", "en": "Total Amount"},
        "order_count": {"zh": "Order Count", "en": "Order Count"},
        "region_name": {"zh": "Region", "en": "Region"},
        "category": {"zh": "Category", "en": "Category"},
        "customer_name": {"zh": "Customer", "en": "Customer"},
        "member_level": {"zh": "Member Level", "en": "Member Level"},
    }
    return aliases.get(col_name, {}).get(language, col_name)
