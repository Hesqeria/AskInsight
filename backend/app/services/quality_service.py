"""Data quality rule generator: DDL -> quality rules (NN/UNIQUE/RANGE/ENUM/TIMELINESS)."""

import re
import json


PRIMARY_KEY_PATTERN = re.compile(
    r'(?:PRIMARY|UNIQUE)\s+KEY.*?[\(]\s*`?(\w+)`?\s*[\)]',
    re.IGNORECASE,
)
COLUMN_PATTERN = re.compile(
    r'`?(\w+)`?\s+(BIGINT|INT|DECIMAL|DOUBLE|FLOAT|VARCHAR|STRING|TEXT|TIMESTAMP|DATETIME|DATE|BOOLEAN)\s*(?:\([^)]*\))?\s*(?:NOT\s+NULL\s*)?(?:\s*COMMENT\s*["\']([^"\']*)["\']\s*)?',
    re.IGNORECASE,
)
ENUM_COMMENT_PATTERN = re.compile(r'IN\s*\(([^)]+)\)', re.IGNORECASE)


def _infer_column_role(name: str) -> str:
    name_lower = name.lower()
    if name_lower.endswith(('_id', '_key', '_code')):
        return 'key'
    if name_lower.endswith(('_time', '_at', '_date', '_ts')):
        return 'timestamp'
    if any(k in name_lower for k in ('amount', 'price', 'qty', 'cnt', 'score', 'rate', 'value')):
        return 'metric'
    if any(k in name_lower for k in ('status', 'type', 'flag', 'state')):
        return 'enum'
    return 'dimension'


def generate_quality_rules(ddl: str) -> dict:
    """Generate data quality rules from a CREATE TABLE DDL.

    Returns:
        {"table_name": str, "rules": [{"field": str, "type": str, "rule": str}]}
    """
    if not ddl or not ddl.strip():
        raise ValueError("Empty DDL")

    ddl_upper = ddl.upper().replace('\n', ' ')
    table_match = re.search(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?', ddl, re.IGNORECASE)
    table_name = table_match.group(1) if table_match else "unknown"

    pk_match = PRIMARY_KEY_PATTERN.search(ddl)
    pk_column = pk_match.group(1) if pk_match else None

    columns = COLUMN_PATTERN.findall(ddl)
    rules = []

    for col_name, col_type, comment in columns:
        col_name = col_name.lower()
        col_type_upper = col_type.upper()
        comment = comment or ''

        role = _infer_column_role(col_name)

        # NOT NULL
        rules.append({
            "field": col_name,
            "type": "not_null",
            "rule": f"{col_name} IS NOT NULL",
            "severity": "high" if role == 'key' else "medium",
        })

        # UNIQUE
        if pk_column and col_name == pk_column.lower():
            rules.append({
                "field": col_name,
                "type": "unique",
                "rule": f"{col_name} has no duplicate values",
                "severity": "high",
            })

        # RANGE
        if col_type_upper in ('BIGINT', 'INT', 'DECIMAL', 'DOUBLE', 'FLOAT'):
            rules.append({
                "field": col_name,
                "type": "range",
                "rule": f"{col_name} >= 0" if role != 'metric' else f"{col_name} >= 0 AND {col_name} <= 99999999",
                "severity": "medium",
            })

        # ENUM
        if role == 'enum' or col_type_upper in ('BOOLEAN',):
            enum_match = ENUM_COMMENT_PATTERN.search(comment)
            if enum_match:
                values = enum_match.group(1).strip()
                rules.append({
                    "field": col_name,
                    "type": "enum",
                    "rule": f"{col_name} IN ({values})",
                    "severity": "medium",
                })

        # TIMELINESS
        if role == 'timestamp' or col_type_upper in ('TIMESTAMP', 'DATETIME', 'DATE'):
            rules.append({
                "field": col_name,
                "type": "timeliness",
                "rule": f"{col_name} <= CURRENT_TIMESTAMP()",
                "severity": "low",
            })

    return {
        "table_name": table_name,
        "column_count": len(columns),
        "rule_count": len(rules),
        "rules": rules,
    }


def generate_batch(rules_result: dict) -> str:
    """Generate DQD (Data Quality Dashboard) SQL batch from rules."""
    table = rules_result["table_name"]
    lines = [f"-- Quality check SQL for {table}", ""]
    for r in rules_result["rules"]:
        lines.append(f"-- [{r['type'].upper()}] {r['field']}: {r['rule']}")
        lines.append(f"SELECT '{r['field']}' AS check_field, '{r['type']}' AS check_type, "
                     f"COUNT(*) AS fail_count FROM {table} WHERE NOT ({r['rule']});")
        lines.append("")
    return "\n".join(lines)
