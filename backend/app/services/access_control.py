"""Access control: RBAC table-level permission + PII field masking.

OPT-M3 (RBAC): check whether the current user's role is allowed to access
every table referenced in the SQL. role_table_perm is the source of truth.

OPT-M4 (PII): mask sensitive fields in query results before returning to
the user. pii_field_config drives which columns get masked and how.

Both services are independent of the SQL string itself — RBAC blocks
unauthorized queries before execution; PII masks returned values without
altering the SELECT.
"""
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import logger


# =====================================================
# OPT-M3: RBAC
# =====================================================

# In-process cache to avoid hitting Doris on every query.
# Format: {role_name: {table_name: allowed}}
_ROLE_PERM_CACHE: dict[str, dict[str, int]] = {}
_CACHE_VERSION = 0  # bump to invalidate


async def load_role_permissions(session: AsyncSession, role_name: str) -> dict[str, int]:
    """Load {table_name: allowed} for a role (with in-process cache)."""
    cached = _ROLE_PERM_CACHE.get(role_name)
    if cached is not None:
        return cached
    try:
        result = await session.execute(
            text("""SELECT table_name, allowed FROM data_agent.role_table_perm
                    WHERE role_name = :r"""),
            {"r": role_name},
        )
        perms = {row[0]: int(row[1]) for row in result.fetchall()}
    except Exception as e:
        logger.warning(f"load_role_permissions failed for {role_name}: {e}; default-deny")
        perms = {}
    _ROLE_PERM_CACHE[role_name] = perms
    return perms


def invalidate_perm_cache() -> None:
    """Bump version to force reload on next call."""
    global _ROLE_PERM_CACHE
    _ROLE_PERM_CACHE = {}


def _extract_tables(sql: str) -> set[str]:
    """Extract table names referenced in FROM/JOIN clauses."""
    if not sql:
        return set()
    # Match FROM/JOIN followed by optional `db.` prefix and table name.
    pattern = re.compile(
        r'(?:FROM|JOIN)\s+`?(?:[a-zA-Z_][\w]*\.)?`?([a-zA-Z_][\w]*)`?',
        re.IGNORECASE,
    )
    tables = set()
    for m in pattern.findall(sql):
        name = m.lower()
        # Skip subquery aliases and reserved words.
        if name in {"dual", "select", "where", "as", "on"}:
            continue
        tables.add(name)
    return tables


async def check_sql_permission(
    session: AsyncSession, sql: str, role_name: str
) -> tuple[bool, str]:
    """Check whether role can access all tables in sql.

    Returns (allowed: bool, reason: str).
    Admin role bypasses the check (admin = full access).
    """
    if not sql:
        return True, "empty sql"
    # Admin bypass.
    if role_name in ("admin", "administrator"):
        return True, "admin bypass"

    tables = _extract_tables(sql)
    if not tables:
        return True, "no tables referenced"

    perms = await load_role_permissions(session, role_name)

    # If role has no entries, default-deny with a clear reason.
    if not perms:
        return False, f"role '{role_name}' has no permissions configured"

    # Pattern-aware grants (Cube/SQLBot consensus): keys may be fnmatch
    # patterns ("ads_*"). Precedence: exact deny(0) > exact allow(1) >
    # pattern allow(1). Exact deny beats pattern allow.
    import fnmatch
    patterns = {k: v for k, v in perms.items()
                if any(c in k for c in "*?")}
    denied = []
    for t in tables:
        if t in perms:
            if perms[t] == 0:
                denied.append(t)
            continue
        if any(v == 1 and fnmatch.fnmatch(t, pat)
               for pat, v in patterns.items()):
            continue
        denied.append(t)
    if denied:
        return False, f"role '{role_name}' denied access to: {', '.join(sorted(denied))}"

    return True, "ok"


# =====================================================
# OPT-M4: PII masking
# =====================================================

# Cache: {table.column: mask_rule}
_PII_CACHE: dict[str, str] = {}
_PII_CACHE_LOADED = False


async def _load_pii_config(session: AsyncSession) -> dict[str, str]:
    """Load {table.column: mask_rule} from pii_field_config (cached)."""
    global _PII_CACHE_LOADED
    if _PII_CACHE_LOADED:
        return _PII_CACHE
    try:
        result = await session.execute(
            text("""SELECT field_id, mask_rule FROM data_agent.pii_field_config
                    WHERE enabled = 1""")
        )
        _PII_CACHE.clear()
        for field_id, rule in result.fetchall():
            _PII_CACHE[field_id.lower()] = rule
        _PII_CACHE_LOADED = True
    except Exception as e:
        logger.warning(f"_load_pii_config failed: {e}; no masking applied")
    return _PII_CACHE


def mask_value(value: Any, rule: str) -> Any:
    """Apply a masking rule to a value."""
    if value is None:
        return None
    s = str(value)
    if not s:
        return s
    if rule == "mask_phone":
        # 13812345678 -> 138****5678
        if len(s) >= 7:
            return s[:3] + "****" + s[-4:]
        return "****"
    if rule == "mask_email":
        # zhangsan@x.com -> z***@x.com
        if "@" in s:
            name, domain = s.split("@", 1)
            if name:
                return name[0] + "***@" + domain
        return "***"
    if rule == "mask_idcard":
        # 110101199001011234 -> 110***********1234
        if len(s) >= 8:
            return s[:3] + "*" * (len(s) - 7) + s[-4:]
        return "****"
    # mask_default: keep first 2 + last 2, mask middle.
    if len(s) <= 4:
        return "****"
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


async def mask_result_pii(
    session: AsyncSession,
    result: list[dict],
    sql: str,
) -> tuple[list[dict], int]:
    """Mask PII fields in a list-of-dict result.

    Args:
        result: query result rows as list of dict (column_name -> value).
        sql:    SQL that produced the result (to detect referenced tables).
    Returns:
        (masked_result, masked_count) — masked_count is the number of cells masked.
    """
    if not result:
        return result, 0

    pii_map = await _load_pii_config(session)
    if not pii_map:
        return result, 0

    tables = _extract_tables(sql)
    if not tables:
        return result, 0

    # Build column -> rule map for tables referenced in this SQL.
    col_rules: dict[str, str] = {}
    for t in tables:
        for fid, rule in pii_map.items():
            if fid.startswith(f"{t}."):
                col = fid.split(".", 1)[1]
                col_rules[col.lower()] = rule
    if not col_rules:
        return result, 0

    masked_count = 0
    for row in result:
        if not isinstance(row, dict):
            continue
        for key in list(row.keys()):
            rule = col_rules.get(str(key).lower())
            if rule and row[key] is not None:
                masked = mask_value(row[key], rule)
                if masked != row[key]:
                    row[key] = masked
                    masked_count += 1
    if masked_count:
        logger.info(f"PII masked {masked_count} cells in result")
    return result, masked_count


# =====================================================
# OPT-M5: EXPLAIN-based cost estimation + sample downgrade
# =====================================================

# Tunable thresholds. Doris EXPLAIN output contains "card=N" estimates
# and "partitions=N/M" pruning info. We use partition ratio + a coarse
# row estimate as a proxy for cost. Adjust based on cluster capacity.
COST_SCAN_PARTITIONS_THRESHOLD = 50   # > 50 partitions scanned → warn
COST_SCAN_ROWS_THRESHOLD = 10_000_000  # > 10M estimated rows → downgrade


async def estimate_query_cost(repo, sql: str) -> dict:
    """Run EXPLAIN and parse a coarse cost estimate.

    Args:
        repo: DwDorisRepository (has .session).
        sql:  the SELECT to estimate.
    Returns:
        {"est_rows": int, "partitions_scanned": int, " partitions_total": int,
         "raw": str, "error": str|None}
    Parsing is best-effort; missing fields default to -1.
    """
    out = {"est_rows": -1, "partitions_scanned": -1, "partitions_total": -1,
           "raw": "", "error": None}
    try:
        from sqlalchemy import text
        result = await repo.session.execute(text(f"EXPLAIN {sql}"))
        rows = result.fetchall()
        raw = "\n".join(str(r[0]) for r in rows)
        out["raw"] = raw[:2000]
        # Parse partitions: "partitionRatio=1/178" or "partitions=2/178"
        import re as _re
        m = _re.search(r"(?:partitionRatio|partitions)\s*=?\s*(\d+)\s*/\s*(\d+)", raw)
        if m:
            out["partitions_scanned"] = int(m.group(1))
            out["partitions_total"] = int(m.group(2))
        # Parse cardinality: "cardinality=12345" or "rows=12345"
        m = _re.search(r"(?:cardinality|rows)\s*=?\s*(\d+)", raw, _re.IGNORECASE)
        if m:
            out["est_rows"] = int(m.group(1))
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def classify_cost(estimate: dict) -> tuple[str, str]:
    """Classify cost as (level, reason).

    level: "ok" | "warn" | "downgrade"
    """
    if estimate.get("error"):
        return "ok", f"explain failed ({estimate['error'][:60]}), skip cost check"
    parts = estimate.get("partitions_scanned", -1)
    rows = estimate.get("est_rows", -1)
    if parts > COST_SCAN_PARTITIONS_THRESHOLD:
        return "warn", f"high partition scan ({parts} partitions)"
    if rows > COST_SCAN_ROWS_THRESHOLD:
        return "downgrade", f"large row estimate ({rows:,} rows) -> consider SAMPLE"
    return "ok", f"cost ok (parts={parts}, rows={rows:,})"


def maybe_inject_sample(sql: str) -> str:
    """Best-effort: inject TABLESAMPLE(10 PERCENT) for large-table scans.

    Conservative: only downgrade single-table SELECTs without subquery,
    keeping the WHERE/GROUP BY/ORDER BY structure intact.
    """
    s = sql.strip()
    # Only single-table (no JOIN).
    if not s.upper().startswith("SELECT") or " JOIN " in s.upper():
        return sql
    # Find "FROM <table>" and inject TABLESAMPLE after it.
    import re as _re
    m = _re.search(r"\bFROM\b\s+([\w.]+)\s*(AS\s+\w+)?", s, _re.IGNORECASE)
    if not m:
        return sql
    tbl = m.group(0)
    sample_clause = f"{tbl} TABLESAMPLE(10 PERCENT) "
    return s.replace(tbl, sample_clause, 1)
