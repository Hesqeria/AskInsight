from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.doris.base import Base


class TableInfoDoris(Base):
    __tablename__ = "table_info"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Table ID")
    name: Mapped[str | None] = mapped_column(String(128), comment="Table name")
    role: Mapped[str | None] = mapped_column(String(32), comment="Table type fact/dim")
    description: Mapped[str | None] = mapped_column(Text, comment="Table description")
