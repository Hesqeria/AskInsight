import sys
from pathlib import Path
from loguru import logger
from app.conf.app_config import app_config

log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>request_id - {extra[request_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def inject_request_id(record):
    from app.core.context import request_id_ctx_var
    try:
        record["extra"]["request_id"] = request_id_ctx_var.get()
    except Exception:
        record["extra"]["request_id"] = "-"


def _filter_secrets(record):
    msg = record.get("message", "")
    for keyword in ("api_key", "password", "token", "secret", "Authorization"):
        if keyword.lower() in str(msg).lower():
            record["message"] = "[REDACTED - contains sensitive data]"
    return True

logger.remove()
logger = logger.patch(_filter_secrets)
logger = logger.patch(inject_request_id)
if app_config.logging.console.enable:
    logger.add(sink=sys.stdout, level=app_config.logging.console.level, format=log_format)
if app_config.logging.file.enable:
    p = Path(app_config.logging.file.path)
    p.mkdir(parents=True, exist_ok=True)
    logger.add(sink=p / "app.log", level=app_config.logging.file.level,
               format=log_format, rotation=app_config.logging.file.rotation,
               retention=app_config.logging.file.retention, encoding="utf-8")
