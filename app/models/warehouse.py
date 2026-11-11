"""Справочник складов: мои склады (FBS) и склады Ozon (FBO)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Warehouse(Base):
    """Склад источника 'seller' (мой) или 'ozon' (склад Ozon).

    Ключ — пара (warehouse_id, source): id у Ozon и моих складов свои.
    """

    __tablename__ = "warehouses"

    warehouse_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    warehouse_type: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    is_rfbs: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    pause_at: Mapped[datetime | None] = mapped_column(DateTime)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"<Warehouse id={self.warehouse_id!r} source={self.source!r}>"
