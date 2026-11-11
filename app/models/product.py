"""Товары: карточка Ozon + юнит-экономика, введённая вручную."""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Product(Base):
    """Товар из Ozon и его юнит-экономика.

    Данные Ozon обновляются при каждой синхронизации, а экономика
    (себестоимость, комиссия, логистика и т.д.) вводится вручную
    и синхронизацией не затирается.
    """

    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Float)
    cost_price: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    commission_rate: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    logistics_cost: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    last_mile_cost: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    packaging_cost: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    offer_id: Mapped[str | None] = mapped_column(Text)
    quantity_fbo: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    quantity_fbs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    commission_rate_ozon_fbo: Mapped[float | None] = mapped_column(Float)
    commission_rate_ozon_fbs: Mapped[float | None] = mapped_column(Float)
    ozon_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"<Product sku={self.sku!r} name={self.name!r}>"
