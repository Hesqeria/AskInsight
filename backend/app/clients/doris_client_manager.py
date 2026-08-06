from typing import Optional
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.conf.app_config import DorisConfig, app_config


class DorisClientManager:
    def __init__(self, config: DorisConfig):
        self.config = config
        self.engine: Optional[AsyncEngine] = None
        self.session_factory: Optional[async_sessionmaker] = None

    def _get_url(self) -> str:
        return (f"mysql+asyncmy://{self.config.user}:{self.config.password}"
                f"@{self.config.host}:{self.config.port}/{self.config.database}?charset=utf8mb4")

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
