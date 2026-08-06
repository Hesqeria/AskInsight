from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ValueDorisRepository:
    """Field-value inverted-index retrieval (Doris MATCH)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def search(self, keyword: str, limit: int = 5):
        if not keyword:
            # Defense: empty keyword must not send MATCH '' (Doris behavior is undefined)
            return []
        sql = ("select * from column_value_info where value match :kw "
               "limit :lt")
        result = await self.session.execute(text(sql), {"kw": keyword, "lt": limit})
        return [dict(row) for row in result.mappings().fetchall()]

    async def search_safe(self, keyword: str, limit: int = 5):
        """Public safe wrapper: returns empty for an empty keyword."""
        if not keyword or not keyword.strip():
            return []
        return await self.search(keyword, limit)

    async def search_exact(self, keyword: str, limit: int = 5):
        """Exact-match dimension values (LIKE), used by the dimension-value exact-match node."""
        if not keyword or not keyword.strip():
            return []
        sql = ("select * from column_value_info where value like :kw "
               "limit :lt")
        result = await self.session.execute(
            text(sql), {"kw": f"%{keyword}%", "lt": limit}
        )
        return [dict(row) for row in result.mappings().fetchall()]
