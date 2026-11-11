"""Репозиторий справочника складов."""

from datetime import datetime

from sqlalchemy import delete, select

from app.models import Warehouse

from .base import BaseRepository, to_datetime


class WarehouseRepository(BaseRepository):
    """Таблица warehouses: мои склады (FBS) и склады Ozon (FBO)."""

    def save(self, items: list[dict], source: str, replace: bool = False) -> int:
        """Сохраняет склады источника. Возвращает число сохраненных записей.

        source — 'seller' (мои склады) или 'ozon' (склады Ozon).
        replace=True удаляет прежние склады этого источника: список моих
        складов в Ozon считается полным, поэтому устаревшие записи не нужны.
        """
        now = datetime.now()
        rows = []
        for item in items:
            warehouse_id = str(item.get("warehouse_id") or "").strip()
            if not warehouse_id:
                continue
            rows.append({
                "warehouse_id": warehouse_id,
                "source": source,
                "name": item.get("name") or "",
                "address": item.get("address") or "",
                "warehouse_type": item.get("warehouse_type") or "",
                "status": item.get("status") or "",
                "is_rfbs": bool(item.get("is_rfbs")),
                "latitude": item.get("latitude"),
                "longitude": item.get("longitude"),
                "pause_at": to_datetime(item.get("pause_at")),
                "synced_at": now,
            })
        if not rows:
            return 0

        if replace:
            self.session.execute(
                delete(Warehouse).where(Warehouse.source == source)
            )

        return self._upsert_many(
            Warehouse,
            rows,
            conflict_cols=["warehouse_id", "source"],
            update_cols=[
                "name",
                "address",
                "warehouse_type",
                "status",
                "is_rfbs",
                "latitude",
                "longitude",
                "pause_at",
                "synced_at",
            ],
        )

    def list_all(self, source: str | None = None) -> list[Warehouse]:
        """Склады по алфавиту id; source — фильтр 'seller' или 'ozon'."""
        stmt = select(Warehouse).order_by(Warehouse.warehouse_id)
        if source:
            stmt = stmt.where(Warehouse.source == source)
        return list(self.session.scalars(stmt))
