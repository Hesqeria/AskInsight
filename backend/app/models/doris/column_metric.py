from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.doris.base import Base


class ColumnMetricDoris(Base):
    __tablename__ = "column_metric"

    column_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Column ID")
    metric_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Metric ID")
