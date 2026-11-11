"""Журнал рекомендаций: что предложили → что сделали → какой результат."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Recommendation(Base):
    """Рекомендация по товару и работа с ней.

    Статусы и типы — справочники из app/recommendations.py.
    """

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="manual", server_default="manual")
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, default="", server_default="")
    result: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(Text, default="new", server_default="new")
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"<Recommendation id={self.id} sku={self.sku!r} status={self.status!r}>"
