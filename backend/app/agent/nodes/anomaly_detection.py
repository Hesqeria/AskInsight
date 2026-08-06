"""Anomaly detection node: run Z-Score/IQR detection on query results -> record baseline + anomaly events"""
import math
import uuid
from datetime import datetime
from langgraph.runtime import Runtime
from sqlalchemy import text

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger

# Anomaly detection thresholds
Z_SCORE_THRESHOLD = 2.0  # |Z| > 2 -> anomaly
MIN_BASELINE_POINTS = 3  # A-B2: need at least 3 baseline points to detect


async def anomaly_detection(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Run anomaly detection on query results + record baseline"""
    writer = runtime.stream_writer
    writer({"stage": "Anomaly Detection"})
    try:
        result = state.get("_last_result", [])
        query = state.get("query", "")

        # A-B3: skip non-numeric results
        if not result or not isinstance(result, list):
            logger.info("Anomaly detection: no numeric results, skipping")
            return {}

        # Extract numeric values
        numeric_values = _extract_numeric(result)
        if not numeric_values:
            logger.info("Anomaly detection: no numeric columns, skipping")
            return {}

        # A-B1: record baseline (recorded on every query)
        meta_repo = runtime.context["meta_doris_repository"]
        for label, value in numeric_values:
            await _record_baseline(meta_repo, query, value, label)

        # A-B2: fetch historical baseline
        baselines = await _get_baselines(meta_repo, query)
        if len(baselines) < MIN_BASELINE_POINTS:
            logger.info(f"Anomaly detection: fewer than {MIN_BASELINE_POINTS} baseline points, skipping detection")
            return {}

        # Compute Z-Score
        baseline_values = [b["value"] for b in baselines]
        avg = sum(baseline_values) / len(baseline_values)
        std = math.sqrt(sum((v - avg) ** 2 for v in baseline_values) / len(baseline_values))

        if std == 0:
            logger.info("Anomaly detection: std deviation is 0, skipping")
            return {}

        # Detect against current value
        current_val = numeric_values[0][1]  # take the first numeric value
        z_score = (current_val - avg) / std

        is_anomaly = abs(z_score) > Z_SCORE_THRESHOLD
        severity = "critical" if abs(z_score) > 3 else "warning" if is_anomaly else "normal"

        logger.info(f"Anomaly detection: current={current_val:.2f}, baseline_avg={avg:.2f}, Z={z_score:.2f}, status={severity}")

        # Record anomaly event
        if is_anomaly:
            await _record_anomaly(meta_repo, query, current_val, avg, std, z_score, severity)

            # SSE push anomaly alert
            writer({"anomaly": {
                "metric": query[:50],
                "current": round(current_val, 2),
                "baseline_avg": round(avg, 2),
                "z_score": round(z_score, 2),
                "severity": severity,
                "message": f"Metric anomaly! Current value {current_val:.2f} deviates from baseline {avg:.2f} (Z={z_score:.2f})",
            }})

        return {"anomaly_status": severity, "anomaly_z_score": round(z_score, 2)}
    except Exception as e:
        logger.error(f"Anomaly detection error: {e}")
        return {}


def _extract_numeric(result: list) -> list:
    """Extract numeric values from query results (label, value)"""
    numeric = []
    if not result:
        return numeric
    for row in result[:10]:
        if isinstance(row, dict):
            for k, v in row.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    numeric.append((k, float(v)))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            try:
                numeric.append((str(row[0]), float(row[1])))
            except (ValueError, TypeError):
                pass
    return numeric


async def _record_baseline(repo, query: str, value: float, label: str):
    """Record a baseline data point"""
    try:
        await repo.session.execute(text("""
            INSERT INTO metric_baseline (id, metric_query, metric_value, metric_label, recorded_at)
            VALUES (:id, :q, :v, :l, :t)
        """), {
            "id": str(uuid.uuid4()),
            "q": query[:500],
            "v": value,
            "l": label[:128],
            "t": datetime.now(),
        })
        await repo.session.commit()
    except Exception as e:
        logger.warning(f"Baseline recording failed: {e}")


async def _get_baselines(repo, query: str) -> list:
    """Fetch historical baseline"""
    try:
        result = await repo.session.execute(text("""
            SELECT metric_value FROM metric_baseline
            WHERE metric_query LIKE :q
            ORDER BY recorded_at DESC LIMIT 30
        """), {"q": f"%{query[:30]}%"})
        rows = result.fetchall()
        return [{"value": r[0]} for r in rows]
    except Exception:
        return []


async def _record_anomaly(repo, query, value, avg, std, z, severity):
    """Record anomaly event"""
    try:
        await repo.session.execute(text("""
            INSERT INTO anomaly_event (id, request_id, metric_query, metric_value,
                baseline_avg, baseline_std, z_score, severity, status, created_at)
            VALUES (:id, '', :q, :v, :ba, :bs, :z, :se, 'open', :t)
        """), {
            "id": str(uuid.uuid4()),
            "q": query[:500], "v": value,
            "ba": avg, "bs": std, "z": z,
            "se": severity, "t": datetime.now(),
        })
        await repo.session.commit()
    except Exception as e:
        logger.warning(f"Anomaly event recording failed: {e}")
