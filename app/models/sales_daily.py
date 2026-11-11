"""Ежедневные продажи по SKU (Ozon Analytics API)."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SalesDaily(Base):
    """Продажи одного SKU за один день. Ключ — пара (sku, day)."""

    __tablename__ = "sales_daily"

    sku: Mapped[str] = mapped_column(Text, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    ordered_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    revenue: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"<SalesDaily sku={self.sku!r} day={self.day}>"
