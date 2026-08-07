"""SQL lineage analysis: table-level and column-level dependency extraction."""

import re


def extract_table_lineage(sql: str) -> dict:
    """Extract table-level lineage from SQL."""
    if not sql or not sql.strip():
        return {"sources": [], "targets": [], "edges": []}

    upper = sql.upper()
    sources_raw = set()
    # WORD_BOUNDARY FROM/JOIN followed by table name
    for pat in [r"\bFROM\s+`?(\w+)`?", r"\bJOIN\s+`?(\w+)`?"]:
        for m in re.findall(pat, upper, re.IGNORECASE):
            sources_raw.add(m.lower())

    targets_raw = set()
    # INSERT INTO target / CREATE TABLE target
    for pat in [r"INSERT\s+(?:INTO|OVERWRITE)\s+(?:TABLE\s+)?`?(\w+)`?",
                r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?"]:
        for m in re.findall(pat, upper, re.IGNORECASE):
            targets_raw.add(m.lower())

    blacklist = {"select", "where", "group", "order", "limit", "as", "join", "on", "and", "or", "not", "by", "from"}
    sources = sorted(sources_raw - blacklist)
    targets = sorted(targets_raw)
    edges = [{"source": s, "target": t} for s in sources for t in targets] if targets else []
    return {"sources": sources, "targets": targets, "edges": edges, "depth": 1}


def extract_column_lineage(sql: str) -> dict:
    """Extract column-level lineage from SELECT statement."""
    if not sql or not sql.strip():
        return {"columns": []}
    upper = sql.upper()
    select_match = re.search(r"\bSELECT\b(.+?)\bFROM\b", upper, re.DOTALL)
    if not select_match:
        return {"columns": []}
    select_part = select_match.group(1).strip()
    columns = []
    for item in select_part.split(","):
        item = item.strip()
        if not item:
            continue
        alias = ""
        alias_match = re.search(r"\bAS\s+`?(\w+)`?\s*$", item, re.IGNORECASE)
        if alias_match:
            alias = alias_match.group(1).lower()
            item = re.sub(r"\bAS\s+`?\w+`?\s*$", "", item, flags=re.IGNORECASE).strip()
        source_table = ""
        source_column = item.lower()
        dot_match = re.match(r"`?(\w+)`?\.`?(\w+)`?$", item)
        if dot_match:
            source_table = dot_match.group(1).lower()
            source_column = dot_match.group(2).lower()
        columns.append({
            "expression": item.lower()[:100],
            "source_table": source_table,
            "source_column": source_column,
            "alias": alias or source_column,
        })
    return {"columns": columns}
