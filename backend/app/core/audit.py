"""Audit log: Doris (primary) -> local file (fallback).

Historically a Kafka->Redis->file chain; both middle tiers were
removed - no consumer existed for the Kafka topic or the Redis list
in this deployment, and the unreachable-broker probe machinery was
a recurring latency hazard. Doris powers admin Audit + PRD section 6
metrics; the local file is the never-silently-drop last resort.
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from app.core.log import logger

AUDIT_FALLBACK_FILE = Path(os.getenv("AUDIT_FALLBACK_FILE", "data/audit_fallback.log"))
AUDIT_FALLBACK_MAX_SIZE = 10 * 1024 * 1024  # 10MB warning threshold


def _write_to_file(log_entry: dict) -> None:
    """Last-resort fallback: append to local file."""
    try:
        AUDIT_FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FALLBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        # Check size and warn
        size = AUDIT_FALLBACK_FILE.stat().st_size
        if size > AUDIT_FALLBACK_MAX_SIZE:
            logger.warning(f"Audit fallback file exceeds {AUDIT_FALLBACK_MAX_SIZE // 1024 // 1024}MB: {size} bytes")
    except Exception as e:
        logger.error(f"Audit file write failed (audit log LOST): {e}")


async def send_audit_log(
    request_id: str,
    username: str,
    query: str,
    sql: str = "",
    status: str = "success",
    latency_ms: int = 0,
    result_rows: int = 0,
    stage: str = "",
    perm_blocked: str = "",
    pii_masked_cells: int = 0,
    safety_violation: str = "",
    extra: dict = None,
):
    """Send audit log; Doris primary, local file fallback.

    OPT-M6 extension fields:
      stage             : pipeline stage (intent/safety/exec/result)
      perm_blocked      : RBAC deny reason (OPT-M3), empty when allowed
      pii_masked_cells  : cells masked in result (OPT-M4)
      safety_violation  : SQL safety violation reason (DROP/...), empty when clean
      extra             : free-form dict for forward compatibility
    """
    log_entry = {
        "id": str(uuid.uuid4()),
        "request_id": request_id,
        "username": username or "anonymous",
        "query": query[:500],
        "sql_text": (sql or "")[:2000],
        "status": status,
        "latency_ms": latency_ms,
        "result_rows": result_rows,
        "stage": stage,
        "perm_blocked": perm_blocked,
        "pii_masked_cells": pii_masked_cells,
        "safety_violation": safety_violation,
        "extra": extra or {},
        "created_at": datetime.now().isoformat(),
    }

    # Tier 0: Doris (persistent; powers §6 success metrics + admin
    # Audit page - the table existed but stayed empty because Kafka was
    # unreachable and Redis/file were the only tiers).
    try:
        from app.clients.doris_client_manager import doris_client_manager
        async with doris_client_manager.session_factory() as s:
            from sqlalchemy import text as _text
            await s.execute(_text(
                "INSERT INTO data_agent.audit_log "
                "(id, request_id, username, query, sql_text, status, "
                " latency_ms, result_rows, created_at) VALUES "
                "(:id, :request_id, :username, :query, :sql_text, :status, "
                " :latency_ms, :result_rows, :created_at)"), {
                "id": log_entry["id"], "request_id": request_id,
                "username": log_entry["username"],
                "query": log_entry["query"],
                "sql_text": log_entry["sql_text"],
                "status": status,
                "latency_ms": latency_ms,
                "result_rows": result_rows,
                "created_at": datetime.now(),
            })
            await s.commit()
        return
    except Exception as e:
        logger.warning(f"Doris audit insert failed, writing to file: {e}")

    # Fallback: local file (never silently drop)
    _write_to_file(log_entry)
