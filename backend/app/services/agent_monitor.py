"""Agent monitoring aggregator (OPT-M10).

Pulls real-time metrics from Prometheus registry + recent audit logs +
eval_runs to produce a dashboard payload. Non-fatal: missing components
return defaults so the dashboard always renders.
"""
from datetime import datetime, timedelta


def _prom_snapshot() -> dict:
    """Read current Prometheus metric values from the default registry."""
    out = {"query_total": {}, "query_latency": {}, "cache_hits": 0,
           "sql_generated": 0, "sql_corrected": 0, "rerank_calls": 0,
           "pii_gate": {}, "clarify_triggered": 0}
    try:
        from prometheus_client import REGISTRY
        for metric in REGISTRY.collect():
            name = metric.name
            if name == "query_total":
                for s in metric.samples:
                    key = f"{s.labels.get('status', '?')}"
                    out["query_total"][key] = s.value
            elif name == "query_latency_count":
                for s in metric.samples:
                    key = f"{s.labels.get('status', '?')}"
                    out["query_latency"].setdefault(key, {})["count"] = s.value
            elif name == "query_latency_sum":
                for s in metric.samples:
                    key = f"{s.labels.get('status', '?')}"
                    out["query_latency"].setdefault(key, {})["sum"] = s.value
            elif name == "cache_hits_total":
                out["cache_hits"] = sum(s.value for s in metric.samples)
            elif name == "sql_generated_total":
                out["sql_generated"] = sum(s.value for s in metric.samples)
            elif name == "sql_corrected_total":
                out["sql_corrected"] = sum(s.value for s in metric.samples)
            elif name == "rerank_calls_total":
                out["rerank_calls"] = sum(s.value for s in metric.samples)
            elif name == "pii_gate_decisions_total":
                for s in metric.samples:
                    out["pii_gate"][s.labels.get("decision", "?")] = s.value
            elif name == "clarify_triggered_total":
                out["clarify_triggered"] = sum(s.value for s in metric.samples)
    except Exception as e:
        out["_error"] = str(e)[:100]
    return out


async def recent_audit_stats(days: int = 1) -> dict:
    """Pull recent query stats from feedback_log + eval_runs."""
    out = {"recent_queries": 0, "good_ratings": 0, "bad_ratings": 0,
           "eval_runs": 0, "eval_avg_l3": 0.0}
    try:
        import pymysql
        import os as _os
        c = pymysql.connect(host=_os.getenv("DORIS_HOST", "192.168.137.52"),
                            port=9030, user=_os.getenv("DORIS_USER", "root"),
                            password=_os.getenv("DORIS_PASSWORD", ""),
                            database="data_agent", charset="utf8mb4")
        cur = c.cursor()
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        # 评分统计
        cur.execute(
            "SELECT COUNT(*) FROM feedback_log WHERE created_at >= %s AND query LIKE %s",
            (since, "[QUALITY_RATING=%%"),
        )
        out["recent_queries"] = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM feedback_log WHERE created_at >= %s AND query LIKE %s",
            (since, "[QUALITY_RATING=1]%"),
        )
        out["good_ratings"] = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM feedback_log WHERE created_at >= %s AND query LIKE %s",
            (since, "[QUALITY_RATING=0]%"),
        )
        out["bad_ratings"] = cur.fetchone()[0]
        # 最近评测 run
        cur.execute(
            "SELECT run_id, AVG(level3_ex) FROM eval_runs WHERE ran_at >= %s GROUP BY run_id ORDER BY MAX(ran_at) DESC LIMIT 1",
            (since,),
        )
        row = cur.fetchone()
        if row:
            out["eval_runs"] = 1
            out["eval_avg_l3"] = float(row[1] or 0)
        c.close()
    except Exception as e:
        out["_audit_error"] = str(e)[:100]
    return out


async def dashboard_payload() -> dict:
    """Aggregate a full dashboard payload."""
    prom = _prom_snapshot()
    audit = await recent_audit_stats(days=1)

    # Compute success rate from prom query_total.
    qtotal = prom.get("query_total", {})
    success = qtotal.get("success", 0)
    error = qtotal.get("error", 0)
    cancelled = qtotal.get("cancelled", 0)
    total = success + error + cancelled
    success_rate = success / total if total else 0.0

    # Compute avg latency.
    lat = prom.get("query_latency", {})
    lat_count = sum(v.get("count", 0) for v in lat.values())
    lat_sum = sum(v.get("sum", 0) for v in lat.values())
    avg_latency = lat_sum / lat_count if lat_count else 0.0

    # Satisfaction rate.
    sat_total = audit["good_ratings"] + audit["bad_ratings"]
    sat_rate = audit["good_ratings"] / sat_total if sat_total else 0.0

    return {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_queries": total,
            "success_rate": round(success_rate, 4),
            "avg_latency_s": round(avg_latency, 3),
            "cache_hits": prom.get("cache_hits", 0),
            "sql_generated": prom.get("sql_generated", 0),
            "sql_corrected": prom.get("sql_corrected", 0),
            "rerank_calls": prom.get("rerank_calls", 0),
            "clarify_triggered": prom.get("clarify_triggered", 0),
        },
        "quality": {
            "satisfaction_rate": round(sat_rate, 4),
            "good_ratings": audit["good_ratings"],
            "bad_ratings": audit["bad_ratings"],
            "eval_runs_today": audit["eval_runs"],
            "eval_avg_l3": round(audit["eval_avg_l3"], 4),
        },
        "security": {
            "pii_gate": prom.get("pii_gate", {}),
        },
        "raw_prom": qtotal,
        "health": {
            "prom_error": prom.get("_error"),
            "audit_error": audit.get("_audit_error"),
        },
    }
