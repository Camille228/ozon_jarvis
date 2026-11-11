"""Дневные срезы остатков: история для поиска зависших товаров."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockSnapshot(Base):
    """Остатки SKU на конкретный день. Ключ — пара (sku, snapshot_date)."""

    __tablename__ = "stock_snapshots"

    sku: Mapped[str] = mapped_column(Text, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    quantity_fbo: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    quantity_fbs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"<StockSnapshot sku={self.sku!r} date={self.snapshot_date}>"
