"""Datasource router with ContextVar isolation (no global race condition)."""

import asyncio
from contextvars import ContextVar
from enum import Enum

from app.core.log import logger


class DataSourceType(str, Enum):
    DORIS = "doris"
    MYSQL = "mysql"
    PG = "pg"


# Default source (module-level, but switch is lock-protected)
_default_source: DataSourceType = DataSourceType.DORIS
# Per-request override (ContextVar isolation)
_request_source: ContextVar = ContextVar("datasource", default=None)
_switch_lock = asyncio.Lock()


async def set_active_source(source: str) -> bool:
    """Switch the default datasource (async, lock-protected)."""
    global _default_source
    try:
        new_source = DataSourceType(source.lower())
    except ValueError:
        logger.error(f"Unknown datasource: {source}, supported: doris/mysql/pg")
        return False
    async with _switch_lock:
        _default_source = new_source
    logger.info(f"Datasource switched: {_default_source.value}")
    return True


def set_request_source(source: str) -> bool:
    """Set datasource for current request only (ContextVar isolation)."""
    try:
        new_source = DataSourceType(source.lower())
        _request_source.set(new_source)
        return True
    except ValueError:
        logger.error(f"Unknown datasource: {source}")
        return False


def get_active_source() -> DataSourceType:
    """Get current datasource (request-scoped if set, else global default)."""
    req = _request_source.get(None)
    return req if req is not None else _default_source


def get_client_manager():
    """Get the client manager for the current datasource."""
    from app.clients.doris_client_manager import doris_client_manager
    from app.clients.mysql_client_manager import mysql_client_manager
    from app.clients.pg_client_manager import pg_client_manager

    source = get_active_source()
    if source == DataSourceType.MYSQL:
        return mysql_client_manager
    elif source == DataSourceType.PG:
        return pg_client_manager
    else:
        return doris_client_manager


def get_repository(session):
    """Get the repository for the current datasource."""
    from app.repositories.doris.dw.dw_doris_repository import DwDorisRepository
    from app.repositories.mysql.mysql_repository import MySQLRepository
    from app.repositories.pg.pg_repository import PGRepository

    source = get_active_source()
    if source == DataSourceType.MYSQL:
        return MySQLRepository(session)
    elif source == DataSourceType.PG:
        return PGRepository(session)
    else:
        return DwDorisRepository(session)
