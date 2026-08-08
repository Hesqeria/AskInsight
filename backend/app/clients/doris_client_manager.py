from typing import Optional
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.conf.app_config import DorisConfig, app_config


class DorisClientManager:
    def __init__(self, config: DorisConfig):
        self.config = config
        self.engine: Optional[AsyncEngine] = None
        self.session_factory: Optional[async_sessionmaker] = None

    def _get_url(self) -> URL:
        from urllib.parse import quote_plus
        return URL.create(
            drivername="mysql+asyncmy",
            username=self.config.user,
            password=self.config.password,
            host=self.config.host,
            port=self.config.port,
            database=self.config.database,
            query={"charset": "utf8mb4"},
        )

    def init(self) -> None:
        self.engine = create_async_engine(
            url=self._get_url(),
            pool_size=5,  # C4-02: halved under multiple workers
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


doris_client_manager = DorisClientManager(app_config.doris)
