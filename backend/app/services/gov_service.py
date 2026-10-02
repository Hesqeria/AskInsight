"""Governance services (GOV-1/2/3/5) - PRD 04-Phase4 Agent数仓落地手册.

GOV-1  asset_inventory      : scan dw vs meta_config_dw.yaml -> asset map
GOV-2  compliance_report    : comment/description/glossary coverage -> traffic light
GOV-3  run_quality_checks   : executable rule engine on ads tables (+ persist results)
GOV-5  governance_overview  : aggregated dashboard payload

Design principles (PRD §4.3):
- rules first, LLM later: deterministic SQL checks here; LLM root-cause
  lives in alert_root_cause_agent (analyzer.py).
- humans decide: failures are *recorded and suggested*, never auto-fixed.
"""
import asyncio
import difflib
import json
import uuid
from datetime import datetime

import yaml
from sqlalchemy import text

from app.core.log import logger
from app.conf.meta_config import load_meta_tables

_DW = "dw"

# Table-name classification heuristics for GOV-1 diff report.
_TEST_PAT = ("tmp", "temp", "test", "mock", "bak", "copy", "_new", "_old",
             "_v2", "debug")


def _classify(name: str) -> str:
    low = name.lower()
    if any(p in low for p in _TEST_PAT):
        return "test_artifact"
    if low.startswith(("dwd_", "dws_", "ads_", "dim_", "ods_")):
        return "warehouse_layer"
    return "legacy"


def _load_yaml_tables() -> dict:
    """{table_name: {"role","description","columns":{...}}} via the
    canonical conf loader (mtime-cached; was re-parsed per call)."""
    out = {}
    for t in load_meta_tables():
        cols = {
            c.get("name", ""): {
                "role": c.get("role", ""),
                "description": c.get("description", ""),
            }
            for c in t.get("columns", [])
        }
        out[t.get("name", "")] = {
            "role": t.get("role", ""),
            "description": t.get("description", ""),
            "columns": cols,
        }
    return out



async def asset_inventory(session) -> dict:
    """GOV-1: one-shot asset inventory + YAML diff (three lists + rename)."""
    rows = (await session.execute(text(
        "SELECT table_name, table_comment, table_rows "
        "FROM information_schema.tables WHERE table_schema = :db"
    ), {"db": _DW})).fetchall()
    dw_tables = {
        r[0]: {"comment": r[1] or "", "rows_est": int(r[2] or 0)}
        for r in rows
    }
    yaml_tables = _load_yaml_tables()

    dw_set, yaml_set = set(dw_tables), set(yaml_tables)
    registered = sorted(dw_set & yaml_set)
    unregistered = sorted(dw_set - yaml_set)
    stale = sorted(yaml_set - dw_set)

    # suspected renames: fuzzy-match stale <-> unregistered
    suspected_renames = []
    for s in stale:
        for u in unregistered:
            ratio = difflib.SequenceMatcher(None, s, u).ratio()
            if ratio >= 0.75:
                suspected_renames.append(
                    {"yaml_table": s, "dw_table": u, "similarity": round(ratio, 2)})
    rename_pairs = {(r["yaml_table"], r["dw_table"]) for r in suspected_renames}

    unregistered_detail = []
    for t in unregistered:
        involved = any(p[1] == t for p in rename_pairs)
        unregistered_detail.append({
            "table": t,
            "class": "suspected_rename" if involved else _classify(t),
            "comment": dw_tables[t]["comment"],
            "rows_est": dw_tables[t]["rows_est"],
        })
    stale_detail = [{
        "table": t,
        "class": "suspected_rename" if any(p[0] == t for p in rename_pairs)
        else "missing_in_dw",
        "yaml_role": yaml_tables[t]["role"],
    } for t in stale]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "database": _DW,
        "dw_table_count": len(dw_set),
        "yaml_table_count": len(yaml_set),
        "registered": {"count": len(registered), "tables": registered},
        "unregistered": {"count": len(unregistered_detail),
                         "tables": unregistered_detail},
        "stale": {"count": len(stale_detail), "tables": stale_detail},
        "suspected_renames": suspected_renames,
        "summary": {
            "registered": len(registered),
            "unregistered": len(unregistered_detail),
            "stale": len(stale_detail),
            "coverage_pct": round(
                100.0 * len(registered) / max(1, len(dw_set)), 1),
        },
    }


async def compliance_report(session) -> dict:
    """GOV-2: standard-compliance (落标) report with traffic light.

    Three signals per table:
      t_comment  : table has non-empty comment (information_schema)
      c_comment  : >=80% columns carry comments (information_schema)
      c_desc     : >=80% yaml-registered columns carry descriptions
    light: green = 3/3, yellow = 1-2, red = 0.
    """
    trows = (await session.execute(text(
        "SELECT table_name, IFNULL(table_comment,'') FROM information_schema.tables "
        "WHERE table_schema = :db"), {"db": _DW})).fetchall()
    crows = (await session.execute(text(
        "SELECT table_name, COUNT(*), "
        "SUM(IF(column_comment != '', 1, 0)) "
        "FROM information_schema.columns WHERE table_schema = :db "
        "GROUP BY table_name"), {"db": _DW})).fetchall()
    c_stats = {r[0]: (int(r[1]), int(r[2] or 0)) for r in crows}
    yaml_tables = _load_yaml_tables()

    per_table = []
    agg = {"green": 0, "yellow": 0, "red": 0}
    t_comment_total = c_comment_total = c_desc_total = table_total = 0
    for tname, tcomment in trows:
        total_cols, commented = c_stats.get(tname, (0, 0))
        has_tc = bool(tcomment.strip())
        cc_pct = round(100.0 * commented / total_cols, 1) if total_cols else 0.0
        has_cc = cc_pct >= 80.0
        ycols = yaml_tables.get(tname, {}).get("columns", {})
        desced = sum(1 for c in ycols.values() if (c["description"] or "").strip())
        cd_pct = round(100.0 * desced / len(ycols), 1) if ycols else None
        has_cd = cd_pct >= 80.0 if cd_pct is not None else False
        score = sum([has_tc, has_cc, has_cd])
        light = "green" if score == 3 else ("red" if score == 0 else "yellow")
        agg[light] += 1
        table_total += 1
        t_comment_total += has_tc
        c_comment_total += has_cc
        c_desc_total += has_cd
        per_table.append({
            "table": tname, "light": light,
            "table_comment": has_tc,
            "column_comment_pct": cc_pct,
            "yaml_desc_pct": cd_pct,
            "yaml_registered": tname in yaml_tables,
        })
    per_table.sort(key=lambda x: ({"red": 0, "yellow": 1, "green": 2}[x["light"]], x["table"]))

    # glossary health (post GOV-0 fix the runtime reads data_agent.glossary)
    try:
        g_total = (await session.execute(text(
            "SELECT COUNT(*) FROM data_agent.glossary"))).scalar()
        g_active = (await session.execute(text(
            "SELECT COUNT(*) FROM data_agent.glossary WHERE status='active'"))).scalar()
        g_bound = (await session.execute(text(
            "SELECT COUNT(*) FROM data_agent.glossary "
            "WHERE table_name IS NOT NULL AND table_name != ''"))).scalar()
    except Exception as e:
        logger.warning(f"gov compliance glossary probe failed: {e}")
        g_total = g_active = g_bound = 0

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "database": _DW,
        "traffic_light": agg,
        "rates": {
            "table_comment_pct": round(100.0 * t_comment_total / max(1, table_total), 1),
            "column_comment_pct": round(100.0 * c_comment_total / max(1, table_total), 1),
            "yaml_desc_pct": round(100.0 * c_desc_total / max(1, table_total), 1),
        },
        "glossary": {"total": g_total, "active": g_active,
                     "bound_to_table": g_bound},
        "tables": per_table,
    }


# --------------------------------------------------------------------------- #
# GOV-3 quality check engine
# --------------------------------------------------------------------------- #
_RESULT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS data_agent.gov_quality_result (
    id          varchar(64) NOT NULL COMMENT 'check id',
    run_id      varchar(64) NULL COMMENT 'batch run id',
    table_name  varchar(64) NULL,
    rule_type   varchar(32) NULL COMMENT 'rowcount/freshness/zero_rate/mock_data/not_null/unique',
    rule_sql    text NULL,
    passed      boolean NULL,
    metric_value double NULL,
    detail      text NULL,
    checked_at  datetime NOT NULL
) UNIQUE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ('replication_num' = '1')
"""

_FRESHNESS_MAX_DAYS = 3
_ZERO_RATE_MAX = 0.5


def _build_rules(table: str, cols: list) -> list:
    """cols: [{name, type, comment, is_key}] -> executable rule dicts."""
    rules = [{
        "rule_type": "rowcount", "col": "",
        "sql": f"SELECT COUNT(*) FROM {_DW}.{table}",
        "pass": lambda v, c=0: v > 0, "detail": "table must have rows",
    }]
    dt_cols = [c for c in cols if c["name"].lower() in ("dt", "stat_date", "date")]
    for c in dt_cols:
        rules.append({
            "rule_type": "freshness", "col": c["name"],
            "sql": f"SELECT DATEDIFF(CURRENT_DATE(), "
                   f"MAX(CAST(`{c['name']}` AS DATE))) FROM {_DW}.{table}",
            "pass": lambda v, c=0: v is not None and v <= _FRESHNESS_MAX_DAYS,
            "detail": f"MAX({c['name']}) within {_FRESHNESS_MAX_DAYS}d",
        })
    for c in cols:
        low = c["name"].lower()
        numeric = c["type"].lower().startswith(
            ("bigint", "int", "double", "decimal", "float", "largeint"))
        if numeric and any(k in low for k in (
                "amount", "gmv", "price", "qty", "quantity",
                "count", "rate", "avg", "roi")):
            rules.append({
                "rule_type": "zero_rate", "col": c["name"],
                "sql": f"SELECT IFNULL(AVG(CASE WHEN `{c['name']}` = 0 "
                       f"THEN 1.0 ELSE 0.0 END), 1.0) FROM {_DW}.{table}",
                "pass": lambda v, c=0: v < _ZERO_RATE_MAX,
                "detail": f"{c['name']} zero-rate < {_ZERO_RATE_MAX} "
                          f"(catches all-zero metric e.g. avg_order_amount=0)",
            })
        if c["is_key"]:
            rules.append({
                "rule_type": "not_null", "col": c["name"],
                "sql": f"SELECT COUNT(*) FROM {_DW}.{table} "
                       f"WHERE `{c['name']}` IS NULL",
                "pass": lambda v, c=0: v == 0,
                "detail": f"{c['name']} (key) no NULLs",
            })
        if any(k in low for k in ("status", "name", "user_name", "nick")):
            rules.append({
                "rule_type": "mock_data", "col": c["name"],
                "sql": f"SELECT COUNT(*) FROM {_DW}.{table} "
                       f"WHERE `{c['name']}` LIKE '模拟%'",
                "pass": lambda v, c=0: v == 0,
                "detail": f"no mock rows ('模拟_*') in {c['name']}",
            })
    return rules


async def run_quality_checks(session, tables=None) -> dict:
    """GOV-3: generate + execute rules on ads tables, persist results.

    Failures never auto-fix: they are recorded with suggested actions
    (fix_action='manual_review') per PRD 修复边界 - humans decide.
    """
    # ensure result table exists (idempotent; Doris IF NOT EXISTS)
    try:
        await session.execute(text(_RESULT_TABLE_DDL))
        await session.commit()
    except Exception as e:
        logger.warning(f"gov_quality_result DDL skipped: {e}")

    if not tables:
        rows = (await session.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=:db AND table_name LIKE 'ads%'"),
            {"db": _DW})).fetchall()
        tables = sorted(r[0] for r in rows)

    run_id = uuid.uuid4().hex[:16]
    results, failures = [], []
    for t in tables:
        crows = (await session.execute(text(
            "SELECT column_name, column_type, column_key "
            "FROM information_schema.columns "
            "WHERE table_schema=:db AND table_name=:t"),
            {"db": _DW, "t": t})).fetchall()
        cols = [{"name": r[0], "type": r[1], "is_key": bool(r[2])} for r in crows]
        for rule in _build_rules(t, cols):
            try:
                val = (await session.execute(text(rule["sql"]))).scalar()
                passed = bool(rule["pass"](val))
            except Exception as e:
                val, passed = None, False
                rule = dict(rule)
                rule["detail"] = f"{rule['detail']} [SQL err: {str(e)[:120]}]"
                try:
                    await session.rollback()
                except Exception:
                    pass
            rec = {
                "id": uuid.uuid4().hex, "run_id": run_id, "table_name": t,
                "rule_type": rule["rule_type"], "rule_sql": rule["sql"],
                "passed": passed, "metric_value": (float(val) if isinstance(val,
                    (int, float)) or val.__class__.__name__ == "Decimal" else None),
                "detail": rule["detail"], "checked_at": datetime.now(),
            }
            results.append(rec)
            if not passed:
                failures.append({k: rec[k] for k in
                                 ("table_name", "rule_type", "detail",
                                  "metric_value")})

    # persist (best effort)
    try:
        if results:
            await session.execute(
                text("INSERT INTO data_agent.gov_quality_result "
                     "(id, run_id, table_name, rule_type, rule_sql, passed, "
                     "metric_value, detail, checked_at) VALUES "
                     "(:id, :run_id, :table_name, :rule_type, :rule_sql, "
                     ":passed, :metric_value, :detail, :checked_at)"),
                results)
            await session.commit()
    except Exception as e:
        logger.warning(f"gov quality persist failed: {e}")
        await session.rollback()

    return {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tables_checked": tables,
        "checks_total": len(results),
        "checks_passed": sum(1 for r in results if r["passed"]),
        "failures": failures,
        "policy": {"auto_fix": "none", "fix_action": "manual_review",
                   "note": "所有失败仅记录+建议,修复由人拍板(PRD §4.3)"},
    }


async def governance_overview(session) -> dict:
    """GOV-5: aggregated governance dashboard payload."""
    # Sequential on purpose: one AsyncSession must not serve two
    # concurrent queries (sqlalchemy "concurrent operations" error).
    inv = await asset_inventory(session)
    comp = await compliance_report(session)
    last_run = (await session.execute(text(
        "SELECT run_id, checked_at, checks_total, checks_passed FROM ("
        " SELECT run_id, checked_at, COUNT(*) checks_total, "
        " SUM(IF(passed,1,0)) checks_passed "
        " FROM data_agent.gov_quality_result GROUP BY run_id, checked_at "
        " ORDER BY checked_at DESC LIMIT 1) t"))).fetchone()
    quality = {"last_run": None}
    if last_run:
        quality["last_run"] = {
            "run_id": last_run[0], "checked_at": str(last_run[1]),
            "checks_total": last_run[2], "checks_passed": last_run[3]}
    _fr = await session.execute(text(
        "SELECT COUNT(*) FROM data_agent.gov_quality_result "
        "WHERE passed = FALSE"))
    open_failures = _fr.scalar() or 0
    quality["open_failures"] = open_failures
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "assets": inv["summary"],
        "compliance": {"traffic_light": comp["traffic_light"],
                       "rates": comp["rates"], "glossary": comp["glossary"]},
        "quality": quality,
        "cost": {"status": "deferred",
                 "note": "GOV-4 降级:审计落库(audit_log)攒数据后再启用(评审意见§四)"},
    }
