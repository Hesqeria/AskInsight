from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.doris_client_manager import doris_client_manager
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.milvus_client_manager import milvus_client_manager
from app.clients.rerank_client_manager import rerank_client_manager
from app.repositories.doris.meta.meta_doris_repository import MetaDorisRepository
from app.repositories.doris.dw.dw_doris_repository import DwDorisRepository
from app.repositories.doris.rl.rl_doris_repository import RlDorisRepository
from app.repositories.doris.value.value_doris_repository import ValueDorisRepository
from app.repositories.milvus.column_milvus_repository import ColumnMilvusRepository
from app.repositories.milvus.metric_milvus_repository import MetricMilvusRepository
from app.services.query_service import QueryService


async def get_meta_session():
    async with doris_client_manager.session_factory() as session:
        yield session


async def get_dw_session():
    async with doris_client_manager.session_factory() as session:
        yield session


async def get_meta_repository(session: AsyncSession = Depends(get_meta_session)):
    return MetaDorisRepository(session)


async def get_rl_repository(session: AsyncSession = Depends(get_meta_session)):
    return RlDorisRepository(session)


async def get_dw_repository(session: AsyncSession = Depends(get_dw_session)):
    return DwDorisRepository(session)


async def get_value_repository(session: AsyncSession = Depends(get_meta_session)):
    return ValueDorisRepository(session)


async def get_column_milvus_repository():
    return ColumnMilvusRepository(milvus_client_manager.client)


async def get_metric_milvus_repository():
    return MetricMilvusRepository(milvus_client_manager.client)


async def get_query_service(
    embedding_client=Depends(lambda: embedding_client_manager.client),
    rerank_client=Depends(lambda: rerank_client_manager.client),
    column_milvus_repository=Depends(get_column_milvus_repository),
    metric_milvus_repository=Depends(get_metric_milvus_repository),
    value_doris_repository=Depends(get_value_repository),
    meta_doris_repository=Depends(get_meta_repository),
    dw_doris_repository=Depends(get_dw_repository),
    rl_repository=Depends(get_rl_repository),
) -> QueryService:
    return QueryService(
        embedding_client=embedding_client,
        rerank_client=rerank_client,
        column_milvus_repository=column_milvus_repository,
        metric_milvus_repository=metric_milvus_repository,
        value_doris_repository=value_doris_repository,
        meta_doris_repository=meta_doris_repository,
        dw_doris_repository=dw_doris_repository,
        rl_repository=rl_repository,
    )
