"""Тесты репозиториев на PostgreSQL (Фаза 1 рефакторинга).

Каждый тест выполняется в транзакции, которая откатывается: реальная
база не загрязняется. Свои записи делаем с уникальными SKU и далёкими
датами, чтобы не пересекаться с реально перенесёнными данными.
"""

from datetime import date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    Document,
    Product,
    Recommendation,
    SalesDaily,
    StockSnapshot,
    Warehouse,
)
from app.repositories import (
    DocumentRepository,
    ProductRepository,
    RecommendationRepository,
    SalesRepository,
    StockSnapshotRepository,
    SyncLogRepository,
    TaxOperationRepository,
    WarehouseRepository,
)


def unique_sku() -> str:
    return f"TEST-{uuid4().hex[:10]}"


def unique_wh_id() -> str:
    return f"WH-{uuid4().hex[:10]}"


def product_item(**overrides) -> dict:
    item = {
        "sku": unique_sku(),
        "offer_id": "OFFER-1",
        "name": "Тестовый товар",
        "price": 1500.0,
        "quantity_fbo": 12,
        "quantity_fbs": 3,
        "commission_rate_ozon_fbo": 0.15,
        "commission_rate_ozon_fbs": 0.18,
        "ozon_updated_at": "2026-11-01T10:00:00",
    }
    item.update(overrides)
    return item


def reload(session, stmt) -> list:
    """Перечитывает из БД: Core-обновления не синхронизируют кэш сессии."""
    session.expire_all()
    return list(session.scalars(stmt))


# --- Товары -----------------------------------------------------------------


def test_upsert_products_creates_new_with_default_economics(db_session):
    repo = ProductRepository(db_session)
    item = product_item()

    assert repo.upsert_products([item]) == 1

    (product,) = reload(
        db_session, select(Product).where(Product.sku == item["sku"])
    )
    assert product.price == 1500.0
    assert product.cost_price == 0
    assert product.logistics_cost == 0
    assert product.last_mile_cost == 0
    assert product.packaging_cost == 0
    # стартовая комиссия нового товара — комиссия Ozon FBS
    assert product.commission_rate == 0.18
    assert product.quantity_fbo == 12
    assert product.quantity_fbs == 3
    assert product.ozon_updated_at == datetime(2026, 11, 1, 10, 0)


def test_upsert_products_falls_back_to_fbo_commission(db_session):
    repo = ProductRepository(db_session)
    item = product_item(commission_rate_ozon_fbs=None)

    repo.upsert_products([item])

    (product,) = reload(
        db_session, select(Product).where(Product.sku == item["sku"])
    )
    assert product.commission_rate == 0.15
    assert product.commission_rate_ozon_fbs is None


def test_upsert_products_keeps_manual_economics(db_session):
    repo = ProductRepository(db_session)
    item = product_item()
    repo.upsert_products([item])
    repo.update_economics(item["sku"], 700.0, 0.1, 50.0, 30.0, 20.0)

    repo.upsert_products([product_item(sku=item["sku"], price=2000.0, quantity_fbo=99)])

    (product,) = reload(
        db_session, select(Product).where(Product.sku == item["sku"])
    )
    assert product.price == 2000.0
    assert product.quantity_fbo == 99
    assert product.cost_price == 700.0
    assert product.commission_rate == 0.1
    assert product.logistics_cost == 50.0
    assert product.last_mile_cost == 30.0
    assert product.packaging_cost == 20.0


def test_update_costs_bulk_updates(db_session):
    repo = ProductRepository(db_session)
    skus = [unique_sku() for _ in range(3)]
    repo.upsert_products([product_item(sku=sku) for sku in skus])

    assert repo.update_costs_bulk({skus[0]: 100, skus[1]: 200}) == 2

    products = reload(db_session, select(Product).where(Product.sku.in_(skus)))
    costs = {p.sku: p.cost_price for p in products}
    assert costs[skus[0]] == 100
    assert costs[skus[1]] == 200
    assert costs[skus[2]] == 0


def test_update_costs_bulk_rejects_negative(db_session):
    repo = ProductRepository(db_session)
    with pytest.raises(ValueError, match="отрицательной"):
        repo.update_costs_bulk({unique_sku(): -5})


def test_update_costs_bulk_rejects_non_numeric(db_session):
    repo = ProductRepository(db_session)
    with pytest.raises(ValueError, match="Некорректная себестоимость"):
        repo.update_costs_bulk({unique_sku(): "много"})


def test_update_cost_and_economics_rowcounts(db_session):
    repo = ProductRepository(db_session)
    sku = unique_sku()
    repo.upsert_products([product_item(sku=sku)])

    assert repo.update_cost(sku, 123.0) == 1
    assert repo.update_economics(sku, 1, 2, 3, 4, 5) == 1
    assert repo.update_cost("НЕТ-ТАКОГО-SKU", 1) == 0


def test_product_get_and_list_all(db_session):
    repo = ProductRepository(db_session)
    item = product_item()
    repo.upsert_products([item])

    assert repo.get(item["sku"]) is not None
    assert repo.get("НЕТ-ТАКОГО-SKU") is None
    assert item["sku"] in {p.sku for p in repo.list_all()}


# --- Журнал синхронизации ---------------------------------------------------


def test_sync_log_add_and_list_recent(db_session):
    repo = SyncLogRepository(db_session)

    first = repo.add("OZON_API", "success", "Загружено товаров: 3")
    second = repo.add("OZON_ANALYTICS", "warning", "Пустая статистика")

    assert first.id is not None
    assert first.started_at is not None
    assert second.id > first.id

    recent = repo.list_recent(limit=2)
    assert [log.message for log in recent] == [
        "Пустая статистика",
        "Загружено товаров: 3",
    ]


# --- Продажи ----------------------------------------------------------------


def test_sales_upsert_daily_updates_known_day(db_session):
    repo = SalesRepository(db_session)
    sku = unique_sku()

    assert repo.upsert_daily([
        {"sku": sku, "day": "2026-11-01", "ordered_units": 5, "revenue": 1000.0}
    ]) == 1
    repo.upsert_daily([
        {"sku": sku, "day": "2026-11-01", "ordered_units": 7, "revenue": 1400.0}
    ])

    rows = reload(db_session, select(SalesDaily).where(SalesDaily.sku == sku))
    assert len(rows) == 1
    assert rows[0].day == date(2026, 11, 1)
    assert rows[0].ordered_units == 7
    assert rows[0].revenue == 1400.0
    assert rows[0].synced_at is not None


def test_sales_upsert_daily_empty(db_session):
    assert SalesRepository(db_session).upsert_daily([]) == 0


def test_sales_for_sku_ordered_by_day(db_session):
    repo = SalesRepository(db_session)
    sku = unique_sku()
    repo.upsert_daily([
        {"sku": sku, "day": "2026-11-02", "ordered_units": 1, "revenue": 10.0},
        {"sku": sku, "day": "2026-11-01", "ordered_units": 2, "revenue": 20.0},
    ])

    days = [row.day for row in repo.for_sku(sku)]
    assert days == [date(2026, 11, 1), date(2026, 11, 2)]


# --- Срезы остатков ---------------------------------------------------------


def test_stock_snapshots_update_same_day(db_session):
    repo = StockSnapshotRepository(db_session)
    sku = unique_sku()

    repo.upsert_many([
        {"sku": sku, "snapshot_date": "2026-11-01", "quantity_fbo": 10, "quantity_fbs": 2}
    ])
    repo.upsert_many([
        {"sku": sku, "snapshot_date": "2026-11-01", "quantity_fbo": 8, "quantity_fbs": 1}
    ])

    rows = reload(
        db_session, select(StockSnapshot).where(StockSnapshot.sku == sku)
    )
    assert len(rows) == 1
    assert rows[0].quantity_fbo == 8
    assert rows[0].quantity_fbs == 1


def test_rows_from_products_use_ozon_date():
    rows = StockSnapshotRepository.rows_from_products(
        [product_item()], now=datetime(2026, 11, 11, 9, 0)
    )
    assert rows[0]["snapshot_date"] == date(2026, 11, 1)
    assert rows[0]["quantity_fbo"] == 12
    assert rows[0]["quantity_fbs"] == 3
    assert rows[0]["synced_at"] == datetime(2026, 11, 11, 9, 0)


def test_backfill_from_products(db_session):
    ProductRepository(db_session).upsert_products([product_item()])

    created = StockSnapshotRepository(db_session).backfill_from_products()

    assert created >= 1


# --- Документы --------------------------------------------------------------


def test_document_add_and_delete(db_session):
    repo = DocumentRepository(db_session)

    document = repo.add(
        "Декларация", "declaration", "SKU-1", "2027-01-31", notes="проверить"
    )
    assert document.id is not None
    assert document.expires_at == date(2027, 1, 31)

    assert repo.delete(document.id) == 1
    assert reload(
        db_session, select(Document).where(Document.id == document.id)
    ) == []


# --- Рекомендации -----------------------------------------------------------


def test_recommendation_add_returns_id(db_session):
    repo = RecommendationRepository(db_session)

    rec_id = repo.add(sku="SKU-1", problem="Проверить цену", kind="low_stock")

    assert rec_id is not None
    (rec,) = reload(
        db_session, select(Recommendation).where(Recommendation.id == rec_id)
    )
    assert rec.status == "new"
    assert rec.kind == "low_stock"
    assert rec.created_at is not None


def test_recommendation_add_validates(db_session):
    repo = RecommendationRepository(db_session)
    with pytest.raises(ValueError, match="не может быть пустой"):
        repo.add(sku="SKU-1", problem="   ")
    with pytest.raises(ValueError, match="Неизвестный статус"):
        repo.add(sku="SKU-1", problem="Проблема", status="непонятный")


def test_add_many_skips_open_duplicates(db_session):
    repo = RecommendationRepository(db_session)
    items = [
        {"sku": unique_sku(), "kind": "low_stock", "problem": "Заканчивается остаток"}
    ]

    assert repo.add_many(items) == 1
    assert repo.add_many(items) == 0  # открытая запись уже есть

    (rec,) = reload(
        db_session, select(Recommendation).where(Recommendation.sku == items[0]["sku"])
    )
    repo.update(rec.id, action="Дозаказали", status="done")

    assert repo.add_many(items) == 1  # после закрытия можно добавить снова


def test_recommendation_update_and_delete(db_session):
    repo = RecommendationRepository(db_session)
    rec_id = repo.add(sku="SKU-1", problem="Проблема")

    assert repo.update(rec_id, action="Действие", result="Итог", status="done") == 1
    (rec,) = reload(
        db_session, select(Recommendation).where(Recommendation.id == rec_id)
    )
    assert rec.action == "Действие"
    assert rec.result == "Итог"
    assert rec.status == "done"

    assert repo.delete(rec_id) == 1
    assert reload(
        db_session, select(Recommendation).where(Recommendation.id == rec_id)
    ) == []


# --- Склады -----------------------------------------------------------------


def test_warehouse_save_upsert_and_replace(db_session):
    repo = WarehouseRepository(db_session)
    wh_id = unique_wh_id()

    assert repo.save(
        [{"warehouse_id": wh_id, "name": "Склад 1", "is_rfbs": True, "latitude": 55.75}],
        source="seller",
    ) == 1
    # тот же склад с новым именем — обновление, а не дубликат
    repo.save([{"warehouse_id": wh_id, "name": "Склад 1 (новое имя)"}], source="seller")

    (warehouse,) = reload(
        db_session, select(Warehouse).where(Warehouse.warehouse_id == wh_id)
    )
    assert warehouse.name == "Склад 1 (новое имя)"
    assert warehouse.is_rfbs is False

    # replace=True вычищает склады источника, которых больше нет в Ozon
    other = unique_wh_id()
    repo.save([{"warehouse_id": other, "name": "Другой"}], source="seller")
    repo.save([{"warehouse_id": wh_id, "name": "Склад 1"}], source="seller", replace=True)

    ids = {
        row.warehouse_id
        for row in reload(
            db_session, select(Warehouse).where(Warehouse.source == "seller")
        )
    }
    assert ids == {wh_id}


# --- Операции АУСН ---------------------------------------------------------


def test_tax_operation_add_and_validation(db_session):
    repo = TaxOperationRepository(db_session)

    op_id = repo.add("2026-11-05", "income", 150_000.50, "Платёж ООО Ромашка")
    assert op_id is not None

    with pytest.raises(ValueError, match="Неизвестный тип операции"):
        repo.add("2026-11-05", "dividend", 100)
    with pytest.raises(ValueError, match="Некорректная сумма"):
        repo.add("2026-11-05", "income", "ноль")
    with pytest.raises(ValueError, match="положительной"):
        repo.add("2026-11-05", "income", -10)
    with pytest.raises(ValueError, match="Укажите дату"):
        repo.add("", "income", 100)
    with pytest.raises(ValueError, match="ГГГГ-ММ-ДД"):
        repo.add("05.11.2026", "income", 100)


def test_tax_operations_list_by_period(db_session):
    repo = TaxOperationRepository(db_session)
    # далёкие даты, чтобы не пересекаться с реальными операциями
    repo.add("2020-01-01", "income", 100)
    repo.add("2020-01-15", "expense", 50)
    repo.add("2020-02-05", "income", 200)

    rows = repo.list_by_period("2020-01-01", "2020-01-31")
    assert [row.operation_date for row in rows] == [date(2020, 1, 15), date(2020, 1, 1)]

    assert [row.operation_date for row in repo.list_by_period(date_from="2020-02-01")] == [
        date(2020, 2, 5)
    ]
    assert repo.list_by_period(date_to="2019-12-31") == []
