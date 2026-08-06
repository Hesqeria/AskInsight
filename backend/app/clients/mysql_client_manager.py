"""MySQL client manager (multi-datasource adapter).

Same interface as DorisClientManager, using the mysql+asyncmy driver.
"""
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.conf.app_config import MySQLConfig


class MySQLClientManager:
    def __init__(self, config: MySQLConfig):
        self.config = config
        self.engine: Optional[AsyncEngine] = None
        self.session_factory: Optional[async_sessionmaker] = None

    def _get_url(self) -> str:
        return (f"mysql+asyncmy://{self.config.user}:{self.config.password}"
                f"@{self.config.host}:{self.config.port}/{self.config.database}?charset=utf8mb4")

    def init(self) -> None:
        self.engine = create_async_engine(
            url=self._get_url(),
            pool_size=5,
            pool_pre_ping=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()


from app.conf.app_config import app_config as _cfg  # noqa: E402

try:
    if hasattr(_cfg, "mysql"):
        mysql_client_manager = MySQLClientManager(_cfg.mysql)
    else:
        mysql_client_manager = None
except Exception:
    mysql_client_manager = None
