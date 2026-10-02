"""Modeling service (PRD P4): Doris DDL drafting per PRD 4.4,
programmatic review per PRD 4.2, and guarded execution."""
import re

from app.core.log import logger

_ALLOWED_TYPES = {
    "TINYINT", "SMALLINT", "INT", "BIGINT", "LARGEINT", "FLOAT", "DOUBLE",
    "DECIMAL", "DATE", "DATETIME", "CHAR", "VARCHAR", "STRING", "BOOLEAN",
}

_KEY_MODEL = {"ads": "UNIQUE", "dwd": "UNIQUE", "dim": "UNIQUE", "ods": "DUPLICATE"}


def draft_ddl(spec: dict) -> str:
    """Build a Doris DDL from a table spec (PRD 4.4 template)."""
    db = spec.get("db") or "dw"
    table = spec["table"]
    table_type = (spec.get("table_type") or "").lower()
    if table_type not in _KEY_MODEL:
        raise ValueError(f"table_type must be one of {sorted(_KEY_MODEL)}")
    columns = spec.get("columns") or []
    if not columns:
        raise ValueError("columns required")
    key_cols = spec.get("key_columns") or [c["name"] for c in columns if c.get("is_key")]
    if not key_cols:
        raise ValueError("key_columns (or columns[].is_key) required")

    col_names = set()
    lines = []
    for c in columns:
        name = c["name"]
        if name in col_names:
            raise ValueError(f"duplicate column {name}")
        col_names.add(name)
        ctype = str(c.get("type", "")).upper().split("(")[0].strip()
        if ctype not in _ALLOWED_TYPES:
            raise ValueError(f"column {name}: type {ctype!r} not allowed")
        typ = c["type"].upper()
        not_null = " NOT NULL" if (name in key_cols and _KEY_MODEL[table_type] == "UNIQUE") else ""
        lines.append(f"    {name} {typ}{not_null} COMMENT '{c.get('comment', '')}'")
    partition_col = spec.get("partition_column") or ("dt" if "dt" in col_names else "")
    if partition_col and partition_col not in col_names:
        raise ValueError(f"partition_column {partition_col} not in columns")
    if partition_col and _KEY_MODEL[table_type] == "UNIQUE":
        # Doris: range partition column must be a leading key column
        key_cols = [partition_col] + [k for k in key_cols if k != partition_col]

    model = _KEY_MODEL[table_type]
    partition_clause = ""
    if partition_col and table_type in ("ads", "dwd"):
        partition_clause = f"PARTITION BY RANGE({partition_col}) ()"

    out = [f"CREATE TABLE IF NOT EXISTS {db}.{table} (",
           ",\n".join(lines),
           f") {model} KEY({', '.join(key_cols)})",
           f"COMMENT '{spec.get('comment', table)}'"]
    if partition_clause:
        out.append(partition_clause)
    out.append(f"DISTRIBUTED BY HASH({', '.join(key_cols[:1])}) BUCKETS {spec.get('buckets', 3)}")
    props = ['"replication_num" = "1"']
    if partition_clause:
        props += ['"dynamic_partition.enable" = "true"',
                  '"dynamic_partition.time_unit" = "DAY"',
                  '"dynamic_partition.start" = "-90"',
                  '"dynamic_partition.end" = "3"',
                  '"dynamic_partition.prefix" = "p"']
    out.append("PROPERTIES (" + ", ".join(props) + ")")
    return "\n".join(out) + ";"


def review_ddl(ddl: str) -> list:
    """PRD 4.2 programmatic checks. Returns violations (empty = compliant)."""
    v = []
    if not ddl or not ddl.strip():
        return ["empty DDL"]
    up = ddl.upper()
    if not re.search(r"(UNIQUE|DUPLICATE|AGGREGATE)\s+KEY\s*\(", up):
        v.append("missing key model (UNIQUE/DUPLICATE/AGGREGATE KEY)")
    if "DISTRIBUTED BY HASH" not in up:
        v.append("missing DISTRIBUTED BY HASH")
    if "REPLICATION_NUM" not in up:
        v.append("missing replication_num property")
    if re.search(r"(ADS|DWD)_[A-Z0-9_]*(INC|DAY)", up) and "DYNAMIC_PARTITION.ENABLE" not in up:
        v.append("inc/day table without dynamic partition")
    key_m = re.search(r"UNIQUE\s+KEY\s*\(([^)]*)\)", up)
    if key_m:
        for kcol in [k.strip() for k in key_m.group(1).split(",")]:
            pat = rf"{kcol}\s+[A-Z]+(\s*\(\d+(\,\s*\d+)?\))?\s+NOT\s+NULL"
            if not re.search(pat, up):
                v.append(f"UNIQUE KEY column {kcol} must be NOT NULL")
    return v


async def execute_ddl(ddl: str) -> dict:
    """Execute a reviewed DDL on Doris. Only CREATE/ALTER TABLE allowed."""
    statement = ddl.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(CREATE\s+TABLE|ALTER\s+TABLE)\b", statement):
        return {"ok": False, "error": "only CREATE/ALTER TABLE statements are allowed"}
    from sqlalchemy import text as _text
    from app.clients.doris_client_manager import doris_client_manager
    doris_client_manager.init()
    async with doris_client_manager.session_factory() as session:
        await session.execute(_text(statement))
        return {"ok": True, "ddl": statement}
