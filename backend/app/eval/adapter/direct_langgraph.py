"""白盒直调 LangGraph Adapter

直接调用 AskInsight 后端的 graph.astream，无需 HTTP。
要求：
- 评测脚本运行在 backend/ 目录下（可 import app.*）
- 复用 main.py 的 lifespan 启动逻辑

设计：
- 通过 main.py 的 lifespan 初始化所有 client/repository
- 调用 graph.astream(stream_mode='values')，从最终 state 取 SQL
- 支持 mock_date：在 add_extra_context 节点注入固定日期，让"今天"等于 dw 数据冻结日
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class _MockDatetime:
    """伪造 datetime 类，让 datetime.today() 返回固定日期

    用法：monkeypatch 替换 add_extra_context 模块的 datetime。
    保留其他类方法（strptime 等）原样。
    """

    def __init__(self, real_dt_class, mock_date: datetime):
        self._real = real_dt_class
        self._mock_date = mock_date

    def today(self):
        return self._mock_date

    def now(self):
        return self._mock_date

    def __getattr__(self, name):
        # 其他属性/方法（strptime、fromtimestamp 等）走真实 datetime
        return getattr(self._real, name)


class DirectLangGraphAdapter:
    """白盒直调 LangGraph

    使用方式：
        async with DirectLangGraphAdapter() as adapter:
            resp = await adapter.ask("昨天 GMV 是多少")
            print(resp.pred_sql)
    """

    def __init__(self, mock_date: Optional[str] = None):
        """
        Args:
            mock_date: 日期字符串 'YYYY-MM-DD'，注入到 add_extra_context 节点
                       让 LLM 看到的"今天"等于此日期（默认 None=用真实日期）
        """
        self._query_service = None
        self._lifespan_ctx = None
        self._mock_date = mock_date
        self._original_datetime = None  # 保存原始 datetime 用于还原

    async def __aenter__(self):
        await self._startup()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._shutdown()

    async def _startup(self):
        """复用 main.py 的 lifespan 启动逻辑"""
        # 触发 jieba 初始化
        import jieba
        jieba.initialize()

        # 复用 main.py 的 client managers
        from app.clients.embedding_client_manager import embedding_client_manager
        from app.clients.rerank_client_manager import rerank_client_manager
        from app.clients.milvus_client_manager import milvus_client_manager
        from app.clients.doris_client_manager import doris_client_manager
        from app.clients.redis_client_manager import redis_client_manager

        embedding_client_manager.init()
        try:
            rerank_client_manager.init()
        except Exception as e:
            logger.warning(f"rerank init failed: {e}")
        milvus_client_manager.init()
        doris_client_manager.init()
        try:
            redis_client_manager.init()
        except Exception as e:
            logger.warning(f"redis init failed: {e}")

        # 手动构造 session 和 repository（绕过 FastAPI Depends）
        from app.repositories.doris.meta.meta_doris_repository import MetaDorisRepository
        from app.repositories.doris.dw.dw_doris_repository import DwDorisRepository
        from app.repositories.doris.value.value_doris_repository import ValueDorisRepository
        from app.repositories.doris.rl.rl_doris_repository import RlDorisRepository
        from app.repositories.milvus.column_milvus_repository import ColumnMilvusRepository
        from app.repositories.milvus.metric_milvus_repository import MetricMilvusRepository
        from app.services.query_service import QueryService

        # 创建 session 并保持打开（评测期间一直使用）
        self._meta_session = doris_client_manager.session_factory()
        self._dw_session = doris_client_manager.session_factory()
        await self._meta_session.__aenter__()
        await self._dw_session.__aenter__()

        self._column_milvus_repo = ColumnMilvusRepository(milvus_client_manager.client)
        self._metric_milvus_repo = MetricMilvusRepository(milvus_client_manager.client)
        self._value_repo = ValueDorisRepository(self._meta_session)
        self._meta_repo = MetaDorisRepository(self._meta_session)
        self._dw_repo = DwDorisRepository(self._dw_session)
        self._rl_repo = RlDorisRepository(self._meta_session)

        self._query_service = QueryService(
            embedding_client=embedding_client_manager.client,
            rerank_client=rerank_client_manager.client,
            column_milvus_repository=self._column_milvus_repo,
            metric_milvus_repository=self._metric_milvus_repo,
            value_doris_repository=self._value_repo,
            meta_doris_repository=self._meta_repo,
            dw_doris_repository=self._dw_repo,
            rl_repository=self._rl_repo,
        )

        # 注入 mock datetime（让 add_extra_context 节点看到固定日期）
        if self._mock_date:
            from app.agent.nodes import add_extra_context
            self._original_datetime = add_extra_context.datetime
            mock_dt = datetime.strptime(self._mock_date, "%Y-%m-%d")
            add_extra_context.datetime = _MockDatetime(datetime, mock_dt)
            logger.info(f"Mock datetime 已注入: add_extra_context 看到的日期 = {self._mock_date}")

        logger.info("DirectLangGraphAdapter 启动完成")

    async def _shutdown(self):
        """释放资源"""
        # 还原 mock datetime
        if self._original_datetime is not None:
            from app.agent.nodes import add_extra_context
            add_extra_context.datetime = self._original_datetime
            logger.info("Mock datetime 已还原")
            self._original_datetime = None

        # 关闭 session
        try:
            await self._meta_session.__aexit__(None, None, None)
            await self._dw_session.__aexit__(None, None, None)
        except Exception:
            pass
        from app.clients.milvus_client_manager import milvus_client_manager
        from app.clients.doris_client_manager import doris_client_manager
        from app.clients.redis_client_manager import redis_client_manager
        milvus_client_manager.close()
        await doris_client_manager.close()
        try:
            await redis_client_manager.close()
        except Exception:
            pass

    async def ask(self, question: str) -> dict:
        """调用 QueryService.query，从 SSE 流解析最终 SQL + 结果

        Returns:
            {
                "pred_sql": "SELECT ...",
                "intent": "query",
                "latency_ms": 1234,
                "error": "",
                "result": [...],  # 最后一条 SQL 的执行结果（如果有）
            }
        """
        if not self._query_service:
            raise RuntimeError("Adapter 未启动，请用 async with")

        # 直接调 graph，跳过 QueryService 缓存/审计层，使用 values 模式拿完整 state
        from app.agent.state import DataAgentState
        from app.agent.context import DataAgentContext
        from app.clients.embedding_client_manager import embedding_client_manager
        from app.clients.rerank_client_manager import rerank_client_manager
        from app.agent.graph import graph

        t0 = time.time()
        final_sql = ""
        last_result = []
        error = ""
        intent = "query"

        state = DataAgentState(
            query=question, error=None, history=[], _username="eval",
        )
        context = DataAgentContext(
            embedding_client=embedding_client_manager.client,
            rerank_client=rerank_client_manager.client,
            column_milvus_repository=self._column_milvus_repo,
            metric_milvus_repository=self._metric_milvus_repo,
            value_doris_repository=self._value_repo,
            meta_doris_repository=self._meta_repo,
            dw_doris_repository=self._dw_repo,
            rl_repository=self._rl_repo,
        )

        try:
            # stream_mode='values': 每个 step 后输出完整 state
            async for s in graph.astream(
                input=state, context=context, stream_mode="values"
            ):
                if not isinstance(s, dict):
                    continue
                # 取最新的 sql（会被 generate_sql / correct_sql 更新）
                if s.get("sql"):
                    final_sql = s["sql"]
                if s.get("error"):
                    error = str(s["error"])[:200]
                if isinstance(s.get("_last_result"), list):
                    last_result = s["_last_result"]
                if s.get("intent"):
                    intent = s.get("intent")
        except Exception as e:
            error = f"agent 异常: {str(e)[:200]}"

        latency_ms = int((time.time() - t0) * 1000)
        return {
            "pred_sql": final_sql,
            "intent": intent,
            "latency_ms": latency_ms,
            "error": error,
            "result": last_result,
        }
