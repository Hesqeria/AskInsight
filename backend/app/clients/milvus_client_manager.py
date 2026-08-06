from typing import Optional
from pymilvus import MilvusClient

from app.conf.app_config import MilvusConfig
from app.core.log import logger


class MilvusClientManager:
    """Milvus 客户端管理器（含自动重连）

    解决 WrenAI #1121（qdrant 交互错误，43 评论）：
      - init 失败不阻塞启动
      - 操作失败时自动重连（最多 3 次）
      - 健康检查方法
    """

    MAX_RECONNECT = 3
    RECONNECT_DELAY = 2  # 秒

    def __init__(self, config: MilvusConfig):
        self.config = config
        self.client: Optional[MilvusClient] = None
        self.connected: bool = False

    def _get_uri(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"

    def init(self) -> None:
        """init 时尝试连接；连接失败不抛，标记 connected=False 供后续重试"""
        try:
            self._do_connect()
        except Exception as e:
            self.client = None
            self.connected = False
            logger.warning(f"Milvus init failed: {e}")

    def _do_connect(self) -> None:
        """实际建立连接"""
        self.client = MilvusClient(
            uri=self._get_uri(),
            user=self.config.user,
            password=self.config.password,
        )
        self.connected = True
        logger.info(f"Milvus connected: {self.config.host}:{self.config.port}")

    def _reconnect(self) -> bool:
        """重连（WrenAI #1121 教训）

        Returns:
            True if reconnected successfully
        """
        import time
        for attempt in range(self.MAX_RECONNECT):
            logger.warning(f"Milvus 重连 attempt {attempt+1}/{self.MAX_RECONNECT}")
            try:
                self.close()
                time.sleep(self.RECONNECT_DELAY)
                self._do_connect()
                return True
            except Exception as e:
                logger.warning(f"Milvus 重连失败: {e}")
        self.connected = False
        logger.error(f"Milvus 重连 {self.MAX_RECONNECT} 次后仍失败")
        return False

    def ensure_connected(self) -> bool:
        """确保已连接，未连接则自动重连

        所有 search/query 操作前调用此方法。
        """
        if self.connected and self.client is not None:
            return True
        return self._reconnect()

    def safe_search(self, collection_name: str, data: list, **kwargs) -> list:
        """带自动重连的 search（WrenAI #1121 教训）

        连接断开时自动重连后重试。
        """
        for attempt in range(self.MAX_RECONNECT):
            if not self.ensure_connected():
                continue
            try:
                return self.client.search(collection_name, data, **kwargs)
            except Exception as e:
                logger.warning(f"Milvus search 失败 (attempt {attempt+1}): {e}")
                self.connected = False
        logger.error(f"Milvus search {self.MAX_RECONNECT} 次后仍失败")
        return []

    def safe_query(self, collection_name: str, filter: str = "", **kwargs) -> list:
        """带自动重连的 query"""
        for attempt in range(self.MAX_RECONNECT):
            if not self.ensure_connected():
                continue
            try:
                return self.client.query(collection_name, filter=filter, **kwargs)
            except Exception as e:
                logger.warning(f"Milvus query 失败 (attempt {attempt+1}): {e}")
                self.connected = False
        logger.error(f"Milvus query {self.MAX_RECONNECT} 次后仍失败")
        return []

    def safe_upsert(self, collection_name: str, data: list, **kwargs):
        """带自动重连的 upsert"""
        for attempt in range(self.MAX_RECONNECT):
            if not self.ensure_connected():
                continue
            try:
                return self.client.upsert(collection_name, data, **kwargs)
            except Exception as e:
                logger.warning(f"Milvus upsert 失败 (attempt {attempt+1}): {e}")
                self.connected = False
        logger.error(f"Milvus upsert {self.MAX_RECONNECT} 次后仍失败")

    def health_check(self) -> dict:
        """健康检查"""
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
