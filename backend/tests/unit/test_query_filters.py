import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import text

from app.services.query_service import QueryService
from app.repositories.doris.dw.dw_doris_repository import DwDorisRepository


@pytest.mark.asyncio
async def test_get_distinct_values_with_search():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.fetchall.return_value = [
        {"value": "华东"}, {"value": "华北"}
    ]
    session.execute.return_value = mock_result

    repo = DwDorisRepository(session)
    values = await repo.get_column_values("dim_region", "region_name", search="华", limit=10, offset=0)

    assert values == ["华东", "华北"]
    session.execute.assert_called_once()
    call_args = session.execute.call_args
    assert "LIKE :search" in str(call_args[0][0])
    assert call_args[0][1]["search"] == "%华%"


@pytest.mark.asyncio
async def test_execute_filtered_sql_applies_filters():
    dw_repo = AsyncMock()
    dw_repo.execute_sql.return_value = [{"region": "华东", "amount": 100}]

    service = QueryService(
        embedding_client=None,
        column_milvus_repository=None,
        metric_milvus_repository=None,
        value_doris_repository=None,
        meta_doris_repository=None,
        dw_doris_repository=dw_repo,
    )

    rows = await service.execute_filtered_sql(
        "SELECT region, amount FROM fact_order",
        [{"field": "region", "operator": "eq", "value": "华东"}],
    )

    assert rows == [{"region": "华东", "amount": 100}]
    dw_repo.execute_sql.assert_called_once()
    called_sql = dw_repo.execute_sql.call_args[0][0]
    assert "WHERE `region` = :f0" in called_sql


@pytest.mark.asyncio
async def test_execute_filtered_sql_rejects_dangerous_sql():
    dw_repo = AsyncMock()
    service = QueryService(
        embedding_client=None,
        column_milvus_repository=None,
        metric_milvus_repository=None,
        value_doris_repository=None,
        meta_doris_repository=None,
        dw_doris_repository=dw_repo,
    )

    with pytest.raises(ValueError, match="SQL safety check failed"):
        await service.execute_filtered_sql("DROP TABLE fact_order", [])

    dw_repo.execute_sql.assert_not_called()
