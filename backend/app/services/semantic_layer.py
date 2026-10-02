"""Semantic layer service (PRD v2.0 §2.2): metric registry tables.

Single caliber entry-point backed by data_agent.idx_metric /
idx_metric_binding. Seeds itself from the in-code MEASURE_REGISTRY on
first use so existing YAML-caliber knowledge is preserved, and exposes
the PRD §4.3 metric-reuse check (先查后建).
"""
import re
from datetime import datetime

from sqlalchemy import text

from app.core.log import logger


def _alias_terms() -> dict:
    """canonical term -> [alias words] (from semantic_grounding)."""
    from app.agent.nodes.semantic_grounding import _BUSINESS_TERM_ALIASES
    out = {}
    for alias, canon in _BUSINESS_TERM_ALIASES.items():
        out.setdefault(canon, set()).add(alias)
    return out


def _caliber_text(business_term: str, binding_col: str) -> str:
    """Pull the YAML caliber description for the bound column."""
    try:
        from app.conf.meta_config import load_meta_tables
        for t in load_meta_tables():
            for c in t.get("columns", []):
                if (t.get("name"), c.get("name")) == tuple(
                        binding_col.split(".")[-2:]):
                    return c.get("description") or ""
    except Exception:
        pass
    return ""


async def seed_from_registry(session) -> int:
    """Import MEASURE_REGISTRY into idx_metric(+binding). Idempotent:
    duplicate keys are skipped via NOT EXISTS check per row."""
    from app.agent.nodes.semantic_grounding import MEASURE_REGISTRY
    aliases = _alias_terms()
    n = 0
    for term, spec in MEASURE_REGISTRY.items():
        row = (await session.execute(text(
            "SELECT COUNT(*) FROM data_agent.idx_metric "
            "WHERE metric_code = :c"), {"c": term})).scalar()
        if row:
            continue
        col_parts = spec["column"].split(".")
        db_name, table_name, col = col_parts[0], col_parts[1], col_parts[-1]
        caliber = _caliber_text(term, spec["column"]) or term
        await session.execute(text(
            "INSERT INTO data_agent.idx_metric "
            "(metric_code, metric_name, aliases, biz_caliber, formula_sql, "
            "unit, time_grain, owner, status, version, updated_at) VALUES "
            "(:c, :n, :a, :cal, :f, '元', 'day', 'seed', 1, 1, NOW())"), {
            "c": term, "n": term,
            "a": ",".join(sorted(aliases.get(term, [])))[:512],
            "cal": caliber[:2000], "f": spec["aggregation"] + "(" + col + ")",
        })
        await session.execute(text(
            "INSERT INTO data_agent.idx_metric_binding "
            "(metric_code, db_name, table_name, col_expr, filter_expr, "
            "dim_bindings, preferred) VALUES "
            "(:c, :db, :t, :col, :flt, NULL, 1)"), {
            "c": term, "db": db_name, "t": table_name,
            "col": spec["aggregation"] + "(" + col + ")",
            "flt": ";".join(spec.get("pre_filters", [])) or None,
        })
        n += 1
    await session.commit()
    return n


async def check_metric_reuse(session, keyword: str) -> list[dict]:
    """PRD §4.3 先查后建: name/alias hit -> reuse list."""
    kw = (keyword or "").strip()
    if not kw:
        return []
    rows = (await session.execute(text(
        "SELECT m.metric_code, m.metric_name, m.biz_caliber, "
        "       b.db_name, b.table_name, b.preferred "
        "FROM data_agent.idx_metric m "
        "LEFT JOIN data_agent.idx_metric_binding b "
        "  ON b.metric_code = m.metric_code "
        "WHERE m.status = 1 AND "
        "      (m.metric_name LIKE :p OR m.aliases LIKE :p "
        "       OR m.metric_code LIKE :p) "
        "LIMIT 20"), {"p": f"%{kw}%"})).mappings().all()
    return [dict(r) for r in rows]


async def preferred_binding(session, metric_code: str) -> dict | None:
    """取数选表规则: preferred=1 first, else ADS>DWS>DWD smallest rows."""
    rows = (await session.execute(text(
        "SELECT b.db_name, b.table_name, b.col_expr, b.filter_expr, "
        "       b.preferred "
        "FROM data_agent.idx_metric_binding b "
        "WHERE b.metric_code = :c"), {"c": metric_code})).mappings().all()
    if not rows:
        return None
    for r in rows:
        if r["preferred"]:
            return dict(r)
    layer_rank = {"ads": 0, "dws": 1, "dwd": 2}

    def rank(r):
        layer = r["table_name"].split("_", 1)[0].lower()
        return layer_rank.get(layer, 9)
    return dict(sorted(rows, key=rank)[0])


async def ensure_seeded(session) -> None:
    """Seed once per process (best-effort, never raises)."""
    try:
        cnt = (await session.execute(text(
            "SELECT COUNT(*) FROM data_agent.idx_metric"))).scalar()
        if not cnt:
            n = await seed_from_registry(session)
            logger.info(f"idx_metric seeded {n} metrics from registry")
    except Exception as e:
        logger.warning(f"idx_metric seed skipped: {e}")
