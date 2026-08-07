"""ETL documentation generator: SQL -> structured description."""

import re


def generate_etl_doc(sql: str, layer: str = "DWD") -> str:
    """Generate an ETL document from SQL.

    Args:
        sql: SQL statement
        layer: target layer (ODS/DWD/DWS/ADS)

    Returns:
        Markdown ETL documentation
    """
    if not sql or not sql.strip():
        return "No valid SQL provided"

    upper = sql.upper()

    # Extract source tables
    from_matches = re.findall(r'\bFROM\s+`?(\w+(?:\.\w+)?)`?', upper, re.IGNORECASE)
    join_matches = re.findall(r'\bJOIN\s+`?(\w+(?:\.\w+)?)`?', upper, re.IGNORECASE)
    source_tables = list(set(from_matches + join_matches))

    # Extract target table
    target_match = re.search(r'INSERT\s+(?:INTO|OVERWRITE)\s+(?:TABLE\s+)?`?(\w+)`?', upper, re.IGNORECASE)
    target_table = target_match.group(1) if target_match else f"{layer.lower()}_target"

    # Extract WHERE conditions
    where_match = re.search(r'\bWHERE\b\s+(.+?)(?:\bGROUP\b|\bORDER\b|\bLIMIT\b|$)', upper, re.DOTALL)
    filters = where_match.group(1).strip()[:300] if where_match else "None"

    # Extract GROUP BY
    group_match = re.search(r'\bGROUP\s+BY\s+(.+?)(?:\bORDER\b|\bLIMIT\b|$)', upper, re.DOTALL)
    aggregation = group_match.group(1).strip()[:200] if group_match else "No aggregation (detail-level)"

    # Extract JOIN conditions
    join_conds = []
    for cond in re.findall(r'\bON\s+(.+?)(?:\bJOIN\b|\bWHERE\b|\bGROUP\b|\bORDER\b|$)', upper, re.DOTALL):
        join_conds.append(cond.strip()[:100])

    # Determine incremental strategy
    has_date_filter = bool(re.search(r'(?:event_date|dt|ds|create_time|update_time)\s*[<>=]', upper, re.IGNORECASE))
    strategy = "append" if "INSERT INTO" in upper.upper() else "overwrite"
    incremental = "daily increment by date" if has_date_filter else "full load"

    doc = f"""## ETL Documentation: {target_table} ({layer} Layer)

### Source Tables
"""
    for t in source_tables:
        doc += f"- `{t}`\n"

    doc += f"""
### Target Table
`{target_table}` ({layer} layer)

### Join Conditions
"""
    if join_conds:
        for jc in join_conds:
            doc += f"- `{jc}`\n"
    else:
        doc += "- Single-source (no joins)\n"

    doc += f"""
### Filters
`{filters}`

### Aggregation
`{aggregation}`

### Incremental Strategy
- **Load mode**: {strategy}
- **Increment type**: {incremental}

### SQL
```sql
{sql[:1000]}
```
"""
    return doc
