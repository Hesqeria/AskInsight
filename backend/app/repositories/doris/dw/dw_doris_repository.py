"""Doris DW repository with identifier sanitization."""
import re
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _safe_identifier(name: str) -> str:
    """Sanitize table/column name: only [a-zA-Z0-9_] allowed, wrapped in backticks."""
    safe = re.sub(r'[^a-zA-Z0-9_]', '_', str(name))[:64]
    if not safe or safe[0].isdigit():
        safe = f"col_{safe}"
    return f"`{safe}`"


class DwDorisRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_column_types(self, table_name: str) -> dict:
        safe_table = _safe_identifier(table_name)
        result = await self.session.execute(text(f"DESCRIBE {safe_table}"))
        return {row.Field: row.Type for row in result.fetchall()}

    async def get_column_values(self, table_name: str, column_name: str, limit: int = 10):
        safe_table = _safe_identifier(table_name)
        safe_col = _safe_identifier(column_name)
        safe_limit = min(max(int(limit), 1), 1000)
        sql = f"SELECT DISTINCT {safe_col} FROM {safe_table} LIMIT {safe_limit}"
        result = await self.session.execute(text(sql))
        return result.scalars().fetchall()

    async def get_db_info(self):
        r = await self.session.execute(text("select version()"))
        version = r.scalar()
        dialect = self.session.get_bind().dialect.name
        return {"version": version, "dialect": dialect}

    async def validate_sql(self, sql: str):
        """Run EXPLAIN on SQL. Caller must have run validate_sql_safety first."""
        safe_sql = str(sql).strip()
        if not safe_sql:
            raise ValueError("Empty SQL for validation")
        await self.session.execute(text(f"explain {safe_sql}"))

    async def execute_sql(self, sql: str):
        """Execute SQL and return rows as list of dicts."""
        safe_sql = str(sql).strip()
        if not safe_sql:
            raise ValueError("Empty SQL for execution")
        result = await self.session.execute(text(safe_sql))
        return [dict(row) for row in result.mappings().fetchall()]
