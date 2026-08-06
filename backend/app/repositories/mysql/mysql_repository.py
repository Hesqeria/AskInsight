"""MySQL Repository (interface-compatible with PGRepository / DwDorisRepository)."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class MySQLRepository:
    """MySQL data warehouse query."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_column_types(self, table_name: str) -> dict[str, str]:
        result = await self.session.execute(text(
            f"SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_NAME = '{table_name}' "
            f"AND TABLE_SCHEMA = DATABASE()"
        ))
        return {row[0]: row[1] for row in result.fetchall()}

    async def get_column_values(self, table_name: str, column_name: str, limit: int = 10):
        sql = f"SELECT DISTINCT `{column_name}` FROM `{table_name}` LIMIT {limit}"
        result = await self.session.execute(text(sql))
        return result.scalars().fetchall()

    async def get_db_info(self):
        result = await self.session.execute(text("SELECT VERSION()"))
        version = result.scalar()
        return {"version": version, "dialect": "mysql"}

    async def validate_sql(self, sql: str):
        await self.session.execute(text(f"EXPLAIN {sql}"))

    async def execute_sql(self, sql: str):
        result = await self.session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]
