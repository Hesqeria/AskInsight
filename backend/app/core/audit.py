"""Audit log: Kafka async -> Redis list -> local file (three-tier fallback)."""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from app.clients.redis_client_manager import redis_client_manager
from app.core.log import logger

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "127.0.0.1:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "data_agent_audit")
AUDIT_FALLBACK_FILE = Path(os.getenv("AUDIT_FALLBACK_FILE", "data/audit_fallback.log"))
AUDIT_FALLBACK_MAX_SIZE = 10 * 1024 * 1024  # 10MB warning threshold

_kafka_producer = None


def _get_producer():
    global _kafka_producer
    if _kafka_producer is None:
        try:
            from kafka import KafkaProducer
            _kafka_producer = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                acks=0,
                retries=0,
            )
            logger.info("Kafka producer connected successfully")
        except Exception as e:
            logger.warning(f"Kafka unavailable, audit will use Redis/file fallback: {e}")
            _kafka_producer = False
    return _kafka_producer


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
):
    """Send audit log with three-tier fallback: Kafka -> Redis -> file."""
    log_entry = {
        "id": str(uuid.uuid4()),
        "request_id": request_id,
        "username": username or "anonymous",
        "query": query[:500],
        "sql_text": (sql or "")[:2000],
        "status": status,
        "latency_ms": latency_ms,
        "result_rows": result_rows,
        "created_at": datetime.now().isoformat(),
    }

    # Tier 1: Kafka
    producer = _get_producer()
    if producer:
        try:
            producer.send(KAFKA_TOPIC, log_entry)
            return
        except Exception as e:
            logger.warning(f"Kafka send failed, trying Redis: {e}")

    # Tier 2: Redis list
    try:
        if redis_client_manager.client:
            await redis_client_manager.client.lpush("audit_fallback", json.dumps(log_entry))
            return
    except Exception as e:
        logger.warning(f"Redis audit fallback failed, writing to file: {e}")

    # Tier 3: Local file (never silently drop)
    _write_to_file(log_entry)
