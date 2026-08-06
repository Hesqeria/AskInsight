"""Audit log: Kafka async delivery + Doris persistence."""
import json
import uuid
from datetime import datetime

from app.clients.redis_client_manager import redis_client_manager
from app.core.log import logger

# Kafka configuration
import os as _os
KAFKA_BOOTSTRAP = _os.getenv("KAFKA_BOOTSTRAP", "127.0.0.1:9092")
KAFKA_TOPIC = _os.getenv("KAFKA_TOPIC", "data_agent_audit")
_kafka_producer = None


def _get_producer():
    global _kafka_producer
    if _kafka_producer is None:
        try:
            from kafka import KafkaProducer
            _kafka_producer = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                acks=0,  # Async, no ack wait
                retries=0,
            )
            logger.info("Kafka producer connected successfully")
        except Exception as e:
            logger.warning(f"Kafka unavailable, audit log downgraded to Redis: {e}")
            _kafka_producer = False  # Mark as unavailable
    return _kafka_producer


async def send_audit_log(
    request_id: str,
    username: str,
    query: str,
    sql: str = "",
    status: str = "success",
    latency_ms: int = 0,
    result_rows: int = 0,
):
    """Send audit log asynchronously (does not block the main flow)."""
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

    producer = _get_producer()
    if producer:
        try:
            producer.send(KAFKA_TOPIC, log_entry)
        except Exception as e:
            logger.warning(f"Kafka send failed: {e}")
    else:
        # Fallback: store in Redis list (background consumer reads and writes to Doris)
        try:
            await redis_client_manager.client.lpush("audit_fallback", json.dumps(log_entry))
        except Exception:
            pass  # Audit log must not block the main flow
