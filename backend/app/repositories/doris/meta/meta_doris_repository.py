from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doris.column_info import ColumnInfoDoris
from app.models.doris.column_metric import ColumnMetricDoris
from app.models.doris.metric_info import MetricInfoDoris
from app.models.doris.table_info import TableInfoDoris


class MetaDorisRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_table_infos(self, table_infos: list[TableInfoDoris]):
        self.session.add_all(table_infos)
        await self.session.commit()

    async def save_column_infos(self, column_infos: list[ColumnInfoDoris]):
        self.session.add_all(column_infos)
        await self.session.commit()

    async def save_metric_infos(self, metric_infos: list[MetricInfoDoris]):
        self.session.add_all(metric_infos)
        await self.session.commit()

    async def save_column_metrics(self, column_metrics: list[ColumnMetricDoris]):
        self.session.add_all(column_metrics)
        await self.session.commit()

    async def get_column_info_by_id(self, column_id: str):
        return await self.session.get(ColumnInfoDoris, column_id)

    async def get_key_columns_by_table_id(self, table_id: str):
        sql = ("select * from column_info where table_id = :table_id "
               "and `role` in ('primary_key', 'foreign_key')")
        result = await self.session.execute(
            select(ColumnInfoDoris).from_statement(text(sql)),
            {"table_id": table_id},
        )
        return result.scalars().fetchall()

    async def get_table_info_by_id(self, table_id: str):
        return await self.session.get(TableInfoDoris, table_id)
