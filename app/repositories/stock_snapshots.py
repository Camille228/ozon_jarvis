"""Репозиторий дневных срезов остатков."""

from datetime import date, datetime

from sqlalchemy import select

from app.models import Product, StockSnapshot

from .base import BaseRepository, to_date, to_datetime


class StockSnapshotRepository(BaseRepository):
    """Таблица stock_snapshots: история остатков для поиска зависших товаров."""

    def upsert_many(self, rows: list[dict]) -> int:
        """Сохраняет срезы; срез за тот же день обновляется, а не дублируется."""
        prepared = []
        for row in rows:
            snapshot_date = to_date(row.get("snapshot_date"))
            if snapshot_date is None:
                continue
            prepared.append({
                "sku": str(row["sku"]),
                "snapshot_date": snapshot_date,
                "quantity_fbo": int(row.get("quantity_fbo") or 0),
                "quantity_fbs": int(row.get("quantity_fbs") or 0),
                "synced_at": to_datetime(row.get("synced_at")) or datetime.now(),
            })

        return self._upsert_many(
            StockSnapshot,
            prepared,
            conflict_cols=["sku", "snapshot_date"],
            update_cols=["quantity_fbo", "quantity_fbs", "synced_at"],
        )

    @staticmethod
    def rows_from_products(items: list[dict], now: datetime | None = None) -> list[dict]:
        """Готовит строки срезов из товаров, полученных от Ozon.

        Срез делаем на дату данных Ozon: если данные свежие — за сегодня.
        """
        now = now or datetime.now()
        rows = []
        for item in items:
            snapshot_date = to_date(item.get("ozon_updated_at")) or now.date()
            rows.append({
                "sku": str(item["sku"]),
                "snapshot_date": snapshot_date,
                "quantity_fbo": int(item.get("quantity_fbo") or 0),
                "quantity_fbs": int(item.get("quantity_fbs") or 0),
                "synced_at": now,
            })
        return rows

    def backfill_from_products(self) -> int:
        """Первый срез из уже загруженных товаров, чтобы история не начиналась с нуля."""
        products = self.session.scalars(select(Product)).all()
        today = date.today()
        rows = [
            {
                "sku": product.sku,
                "snapshot_date": product.ozon_updated_at.date()
                if product.ozon_updated_at
                else today,
                "quantity_fbo": product.quantity_fbo,
                "quantity_fbs": product.quantity_fbs,
                "synced_at": datetime.now(),
            }
            for product in products
        ]
        return self.upsert_many(rows)
