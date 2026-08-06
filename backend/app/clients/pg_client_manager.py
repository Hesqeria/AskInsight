"""PostgreSQL client manager (multi-datasource adapter v4)."""
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.conf.app_config import PGConfig


class PGClientManager:
    def __init__(self, config: PGConfig):
        self.config = config
        self.engine: Optional[AsyncEngine] = None
        self.session_factory: Optional[async_sessionmaker] = None

    def _get_url(self) -> str:
        from urllib.parse import quote_plus
        pwd = quote_plus(self.config.password)
        return (f"postgresql+asyncpg://{self.config.user}:{pwd}"
                f"@{self.config.host}:{self.config.port}/{self.config.database}")

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


# Global singleton (loaded from app_config)
try:
    from app.conf.app_config import app_config as _cfg
    if hasattr(_cfg, 'pg'):
        pg_client_manager = PGClientManager(_cfg.pg)
    else:
        pg_client_manager = None
except Exception:
    pg_client_manager = None
