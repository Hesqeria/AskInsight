from sqlalchemy import String, Text
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.doris.base import Base


class ColumnInfoDoris(Base):
    __tablename__ = "column_info"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Column ID")
    name: Mapped[str | None] = mapped_column(String(128), comment="Column name")
    type: Mapped[str | None] = mapped_column(String(64), comment="Data type")
    role: Mapped[str | None] = mapped_column(String(32), comment="Column role")
    examples: Mapped[dict | list | None] = mapped_column(JSON, comment="Data examples")
    description: Mapped[str | None] = mapped_column(Text, comment="Column description")
    alias: Mapped[dict | list | None] = mapped_column(JSON, comment="Column alias")
    table_id: Mapped[str | None] = mapped_column(String(64), comment="Owning table ID")
