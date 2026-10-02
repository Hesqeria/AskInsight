"""sqlglot-based column-level lineage extraction (PRD P3).

Replaces regex parsing with a real AST. Returns the same record shape
as the legacy implementation so callers/storage stay unchanged:
    [{"target_column", "source_table", "source_column", "transformation"}]
"""
from sqlglot import exp
import sqlglot

from app.core.log import logger


def extract_lineage_sqlglot(sql: str):
    """Return lineage records, or None when the SQL cannot be parsed
    (caller falls back to the legacy regex path)."""
    try:
        tree = sqlglot.parse_one(sql, read="mysql")
    except Exception as e:
        logger.debug(f"sqlglot parse failed: {e}")
        return None

    if tree.find(exp.Select) is None:
        return []  # DDL/DML/message -> no lineage

    # alias / name -> full table name, excluding CTE aliases
    cte_names = set()
    for cte in tree.find_all(exp.CTE):
        cte_names.add(cte.alias_or_name.lower())

    alias_map = {}
    for t in tree.find_all(exp.Table):
        name = t.name
        full = f"{t.db}.{name}" if t.db else name
        if name.lower() in cte_names:
            continue
        for key in {t.alias_or_name, name}:
            if key:
                alias_map[key.lower()] = full

    lineage, seen = [], set()
    for select in tree.find_all(exp.Select):
        for sel in select.selects:
            if isinstance(sel, exp.Alias):
                target = sel.alias
                expr = sel.this
            else:
                target = sel.sql()
                expr = sel
            if expr is None or isinstance(expr, exp.Star) or target == "*":
                continue
            func_name = "DIRECT"
            if isinstance(expr, exp.Case):
                func_name = "CASE"
            elif isinstance(expr, exp.Func):
                func_name = (expr.sql_name() or expr.key).upper()
            for col in expr.find_all(exp.Column):
                tref = col.table or ""
                src_table = alias_map.get(tref.lower(), tref)
                rec = {
                    "target_column": target,
                    "source_table": src_table,
                    "source_column": col.name,
                    "transformation": func_name,
                }
                key = (rec["target_column"], rec["source_table"],
                       rec["source_column"], rec["transformation"])
                if key in seen:
                    continue
                seen.add(key)
                lineage.append(rec)
    return lineage
