from typing import Optional
from pymilvus import MilvusClient

from app.conf.app_config import MilvusConfig
from app.core.log import logger


class MilvusClientManager:
    """Milvus client manager (with auto-reconnect).

    Addresses WrenAI #1121 (qdrant interaction error, 43 comments):
      - init failure does not block startup
      - auto-reconnect on operation failure (up to 3 times)
      - health check method
    """

    MAX_RECONNECT = 3
    RECONNECT_DELAY = 2  # seconds

    def __init__(self, config: MilvusConfig):
        self.config = config
        self.client: Optional[MilvusClient] = None
        self.connected: bool = False

    def _get_uri(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"

    def init(self) -> None:
        """On init, attempt to connect; on failure do not raise, just mark connected=False for later retry."""
        try:
            self._do_connect()
        except Exception as e:
            self.client = None
            self.connected = False
            logger.warning(f"Milvus init failed: {e}")

    def _do_connect(self) -> None:
        """Actually establish the connection."""
        self.client = MilvusClient(
            uri=self._get_uri(),
            user=self.config.user,
            password=self.config.password,
        )
        self.connected = True
        logger.info(f"Milvus connected: {self.config.host}:{self.config.port}")

    def _reconnect(self) -> bool:
        """Reconnect (WrenAI #1121 lesson).

        Returns:
            True if reconnected successfully
        """
        import time
        for attempt in range(self.MAX_RECONNECT):
            logger.warning(f"Milvus reconnect attempt {attempt+1}/{self.MAX_RECONNECT}")
            try:
                self.close()
                time.sleep(self.RECONNECT_DELAY)
                self._do_connect()
                return True
            except Exception as e:
                logger.warning(f"Milvus reconnect failed: {e}")
        self.connected = False
        logger.error(f"Milvus still failed after {self.MAX_RECONNECT} reconnect attempts")
        return False

    def ensure_connected(self) -> bool:
        """Ensure connected; auto-reconnect if not.

        Call this before any search/query operation.
        """
        if self.connected and self.client is not None:
            return True
        return self._reconnect()

    def safe_search(self, collection_name: str, data: list, **kwargs) -> list:
        """search with auto-reconnect (WrenAI #1121 lesson).

        Auto-reconnects and retries when the connection is dropped.
        """
        for attempt in range(self.MAX_RECONNECT):
            if not self.ensure_connected():
                continue
            try:
                return self.client.search(collection_name, data, **kwargs)
            except Exception as e:
                logger.warning(f"Milvus search failed (attempt {attempt+1}): {e}")
                self.connected = False
        logger.error(f"Milvus search still failed after {self.MAX_RECONNECT} attempts")
        return []

    def safe_query(self, collection_name: str, filter: str = "", **kwargs) -> list:
        """query with auto-reconnect."""
        for attempt in range(self.MAX_RECONNECT):
            if not self.ensure_connected():
                continue
            try:
                return self.client.query(collection_name, filter=filter, **kwargs)
            except Exception as e:
                logger.warning(f"Milvus query failed (attempt {attempt+1}): {e}")
                self.connected = False
        logger.error(f"Milvus query still failed after {self.MAX_RECONNECT} attempts")
        return []

    def safe_upsert(self, collection_name: str, data: list, **kwargs):
        """upsert with auto-reconnect."""
        for attempt in range(self.MAX_RECONNECT):
            if not self.ensure_connected():
                continue
            try:
                return self.client.upsert(collection_name, data, **kwargs)
            except Exception as e:
                logger.warning(f"Milvus upsert failed (attempt {attempt+1}): {e}")
                self.connected = False
        logger.error(f"Milvus upsert still failed after {self.MAX_RECONNECT} attempts")

    def health_check(self) -> dict:
        """Health check."""
        try:
            if self.connected and self.client is not None:
                return {"status": "ok", "host": self.config.host, "connected": True}
            return {"status": "disconnected", "host": self.config.host, "connected": False}
        except Exception as e:
            return {"status": "error", "host": self.config.host, "error": str(e)}

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.connected = False


from app.conf.app_config import app_config as _cfg  # noqa: E402
milvus_client_manager = MilvusClientManager(_cfg.milvus)
