"""Операции для калькулятора АУСН: доходы и расходы."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaxOperation(Base):
    """Доход или расход для расчёта АУСН. Типы — справочник из app/tax.py."""

    __tablename__ = "tax_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime | None] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"<TaxOperation id={self.id} kind={self.kind!r} amount={self.amount}>"
