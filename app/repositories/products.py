"""Репозиторий товаров: карточки Ozon + юнит-экономика."""

from datetime import datetime

from sqlalchemy import bindparam, select, update

from app.models import Product

from .base import BaseRepository, to_datetime


class ProductRepository(BaseRepository):
    """Таблица products.

    Поведение перенесено из app/database.py (Фаза 1 — без изменений).
    Записи среза остатков и журнала синхронизации делает сервисный
    слой (Фаза 2): см. StockSnapshotRepository.rows_from_products.
    """

    def upsert_products(self, items: list[dict]) -> int:
        """Сохраняет товары, полученных от Ozon. Возвращает число товаров.

        Экономику, введённую вручную (себестоимость, комиссия, логистика
        и т.д.), при повторной синхронизации не трогаем — обновляем только
        данные Ozon. Новым товарам подставляем комиссию Ozon как стартовое
        значение (FBS, иначе FBO).
        """
        if not items:
            return 0

        now = datetime.now()
        rows = []
        for item in items:
            # Стартовая комиссия для нового товара: берём FBS, иначе FBO
            commission_default = (
                item.get("commission_rate_ozon_fbs")
                if item.get("commission_rate_ozon_fbs") is not None
                else item.get("commission_rate_ozon_fbo")
            )
            rows.append({
                "sku": str(item["sku"]),
                "offer_id": item.get("offer_id"),
                "name": item["name"],
                "price": item.get("price") or 0,
                "quantity_fbo": int(item.get("quantity_fbo") or 0),
                "quantity_fbs": int(item.get("quantity_fbs") or 0),
                "commission_rate_ozon_fbo": item.get("commission_rate_ozon_fbo"),
                "commission_rate_ozon_fbs": item.get("commission_rate_ozon_fbs"),
                "cost_price": 0,
                "commission_rate": commission_default
                if commission_default is not None
                else 0,
                "logistics_cost": 0,
                "last_mile_cost": 0,
                "packaging_cost": 0,
                "ozon_updated_at": to_datetime(item.get("ozon_updated_at")) or now,
                "updated_at": now,
            })

        return self._upsert_many(
            Product,
            rows,
            conflict_cols=["sku"],
            update_cols=[
                "offer_id",
                "name",
                "price",
                "quantity_fbo",
                "quantity_fbs",
                "commission_rate_ozon_fbo",
                "commission_rate_ozon_fbs",
                "ozon_updated_at",
                "updated_at",
            ],
        )

    def get(self, sku: str) -> Product | None:
        return self.session.get(Product, sku)

    def list_all(self) -> list[Product]:
        return list(self.session.scalars(select(Product).order_by(Product.sku)))

    def update_cost(self, sku: str, cost: float) -> int:
        """Обновляет себестоимость товара по SKU. Возвращает число строк."""
        result = self.session.execute(
            update(Product).where(Product.sku == sku).values(cost_price=cost)
        )
        return result.rowcount

    def update_economics(
        self,
        sku: str,
        cost_price: float,
        commission_rate: float,
        logistics_cost: float,
        last_mile_cost: float,
        packaging_cost: float,
    ) -> int:
        """Обновляет юнит-экономику товара по SKU. Возвращает число строк."""
        result = self.session.execute(
            update(Product)
            .where(Product.sku == sku)
            .values(
                cost_price=cost_price,
                commission_rate=commission_rate,
                logistics_cost=logistics_cost,
                last_mile_cost=last_mile_cost,
                packaging_cost=packaging_cost,
            )
        )
        return result.rowcount

    def update_costs_bulk(self, costs: dict) -> int:
        """Массово обновляет себестоимость: {sku: себестоимость} → число строк.

        Отрицательная или нечисловая себестоимость не записывается: это
        ошибка ввода.
        """
        prepared = []
        for sku, cost in costs.items():
            try:
                value = float(cost)
            except (TypeError, ValueError):
                raise ValueError(f"Некорректная себестоимость для SKU {sku}: {cost!r}")
            if value < 0:
                raise ValueError(f"Себестоимость не может быть отрицательной: SKU {sku}")
            prepared.append({"b_sku": str(sku), "b_cost": value})

        if not prepared:
            return 0

        # Чистый Core-запрос по таблице: ORM bulk-режим SQLAlchemy требует
        # для executemany передавать первичные ключи по другой схеме.
        # Имена bind-параметров отличаются от колонок: они зарезервированы.
        products_table = Product.__table__
        stmt = (
            update(products_table)
            .where(products_table.c.sku == bindparam("b_sku"))
            .values(cost_price=bindparam("b_cost"))
        )
        result = self.session.execute(stmt, prepared)
        return result.rowcount
