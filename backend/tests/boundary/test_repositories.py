"""B3.1-B3.3: Repository layer boundary tests"""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_b31_get_column_info_by_id_nonexistent():
    """B3.1: querying a non-existent id should return None without raising"""
    from app.repositories.doris.meta.meta_doris_repository import MetaDorisRepository
    session = MagicMock()
    session.get = AsyncMock(return_value=None)
    repo = MetaDorisRepository(session)
    result = await repo.get_column_info_by_id("nonexistent.id")
    assert result is None


@pytest.mark.asyncio
async def test_b32_value_doris_search_empty_keyword():
    """B3.2: empty-keyword MATCH should return an empty list (Doris MATCH '' behavior is handled by the application layer fallback)"""
    from app.repositories.doris.value.value_doris_repository import ValueDorisRepository
    session = MagicMock()
    # simulate empty return
    fake_result = MagicMock()
    fake_result.fetchall = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=fake_result)
    repo = ValueDorisRepository(session)
    # the application layer should return [] directly when keyword is empty, instead of sending MATCH ''
    result = await repo.search_safe("")
    assert result == []


def test_b33_milvus_search_empty_vector():
    """B3.3: empty-vector search should raise ValueError"""
    from app.repositories.milvus.column_milvus_repository import ColumnMilvusRepository
    client = MagicMock()
    repo = ColumnMilvusRepository(client)
    # the application layer should validate the vector length before searching
    with pytest.raises(ValueError):
        repo.search_safe([])
