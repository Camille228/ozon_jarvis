"""Репозиторий ежедневных продаж по SKU (Ozon Analytics API)."""

from datetime import datetime

from sqlalchemy import select

from app.models import SalesDaily

from .base import BaseRepository, to_date


class SalesRepository(BaseRepository):
    """Таблица sales_daily."""

    def upsert_daily(self, items: list[dict]) -> int:
        """Сохраняет продажи по дням, обновляя уже известные дни.

        Возвращает число сохраненных строк. Пустой список — 0 строк;
        предупреждение в журнал пишет сервисный слой (Фаза 2), как это
        делал старый app/database.py.
        """
        now = datetime.now()
        rows = []
        for item in items:
            day = to_date(item["day"])
            if day is None:
                continue
            rows.append({
                "sku": str(item["sku"]),
                "day": day,
                "ordered_units": int(item.get("ordered_units") or 0),
                "revenue": float(item.get("revenue") or 0),
                "synced_at": now,
            })

        return self._upsert_many(
            SalesDaily,
            rows,
            conflict_cols=["sku", "day"],
            update_cols=["ordered_units", "revenue", "synced_at"],
        )

    def for_sku(self, sku: str) -> list[SalesDaily]:
        """Продажи одного SKU от старых дней к новым."""
        stmt = select(SalesDaily).where(SalesDaily.sku == sku).order_by(SalesDaily.day)
        return list(self.session.scalars(stmt))
