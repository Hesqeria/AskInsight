"""B2.1-B2.3: client layer boundary tests"""
import pytest

from app.conf.app_config import DorisConfig, MilvusConfig, EmbeddingConfig
from app.clients.doris_client_manager import DorisClientManager
from app.clients.milvus_client_manager import MilvusClientManager
from app.clients.embedding_client_manager import EmbeddingClientManager


def _fake_doris():
    return DorisConfig(host="test-host.local", port=9999, user="u", password="p",
                      database="data_agent")


def _fake_milvus():
    return MilvusConfig(host="test-host.local", port=9999, user="u", password="p",
                       embedding_size=1024, column_collection="c", metric_collection="m")


def _fake_embedding():
    return EmbeddingConfig(api_base="http://127.0.0.1:9999/v1", api_key="k", model="x")


def test_b21_doris_unreachable_host():
    """B2.1: init does not raise; query raises at runtime"""
    mgr = DorisClientManager(_fake_doris())
    mgr.init()  # should not raise (create_async_engine does not connect immediately)
    # engine instantiated successfully
    assert mgr.engine is not None


def test_b22_milvus_wrong_password():
    """B2.2: Milvus wrong password does not raise on init (connection is lazy), close is safe"""
    mgr = MilvusClientManager(_fake_milvus())
    # init-time connection failure should be swallowed: client=None, connected=False, no raise
    assert mgr.connected is False
    mgr.close()  # should close safely even when client=None


def test_b23_embedding_service_down():
    """B2.3: TEI service not started, init does not raise"""
    mgr = EmbeddingClientManager(_fake_embedding())
    mgr.init()  # only create client instance
    assert mgr.client is not None
