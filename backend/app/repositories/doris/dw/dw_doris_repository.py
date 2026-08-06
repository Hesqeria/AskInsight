from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DwDorisRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_column_types(self, table_name: str) -> dict[str, str]:
        result = await self.session.execute(text(f"DESCRIBE {table_name}"))
        return {row.Field: row.Type for row in result.fetchall()}

    async def get_column_values(self, table_name: str, column_name: str, limit: int = 10):
        sql = f"SELECT DISTINCT `{column_name}` FROM {table_name} LIMIT {limit}"
        result = await self.session.execute(text(sql))
        return result.scalars().fetchall()

    async def get_db_info(self):
        r = await self.session.execute(text("select version()"))
        version = r.scalar()
        dialect = self.session.get_bind().dialect.name
        return {"version": version, "dialect": dialect}

    async def validate_sql(self, sql: str):
        await self.session.execute(text(f"explain {sql}"))

    async def execute_sql(self, sql: str):
        result = await self.session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]
