"""Data source router selector.

Automatically selects Doris / MySQL / PostgreSQL data source based on config.
Supports runtime switching.
"""
from enum import Enum

from app.core.log import logger


class DataSourceType(str, Enum):
    DORIS = "doris"
    MYSQL = "mysql"
    PG = "pg"


_active_source: DataSourceType = DataSourceType.DORIS


def set_active_source(source: str):
    """Switch the active data source."""
    global _active_source
    try:
        _active_source = DataSourceType(source.lower())
        logger.info(f"Data source switched: {_active_source.value}")
    except ValueError:
        logger.error(f"Unknown data source: {source}, supported: doris/mysql/pg")


def get_active_source() -> DataSourceType:
    return _active_source


def get_client_manager():
    """Get the client manager for the current data source."""
    from app.clients.doris_client_manager import doris_client_manager
    from app.clients.mysql_client_manager import mysql_client_manager
    from app.clients.pg_client_manager import pg_client_manager

    if _active_source == DataSourceType.MYSQL:
        return mysql_client_manager
    elif _active_source == DataSourceType.PG:
        return pg_client_manager
    else:
        return doris_client_manager


def get_repository(session):
    """Get the Repository for the current data source."""
    from app.repositories.doris.dw.dw_doris_repository import DwDorisRepository
    from app.repositories.mysql.mysql_repository import MySQLRepository
    from app.repositories.pg.pg_repository import PGRepository

    if _active_source == DataSourceType.MYSQL:
        return MySQLRepository(session)
    elif _active_source == DataSourceType.PG:
        return PGRepository(session)
    else:
        return DwDorisRepository(session)
