"""Тесты сервиса синхронизации товаров и продаж (Фаза 2).

Сервисы открывают свои сессии через фабрику session_factory, привязанную
к внешней транзакции: commit сервиса фиксирует savepoint, а teardown
откатывает всё — реальная база не загрязняется.

Внешняя транзакция видит и уже закоммиченные реальные данные, поэтому
записи журнала проверяем не «по всему журналу», а только созданные
после начала теста (logs_after).
"""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import Product, SalesDaily, StockSnapshot, SyncLog
from app.repositories import ProductRepository
from app.services import sync as sync_service
from app.services.ozon_client import OzonApiError
from test_repositories import product_item, unique_sku


def max_log_id(session_factory) -> int:
    """Максимальный id записи журнала на момент вызова."""
    with session_factory() as session:
        return session.scalar(select(func.max(SyncLog.id))) or 0


def logs_after(session_factory, log_id: int) -> list[tuple]:
    """Записи журнала, созданные после id (только то, что написал сам тест)."""
    with session_factory() as session:
        stmt = select(SyncLog).where(SyncLog.id > log_id).order_by(SyncLog.id)
        return [(log.source, log.status, log.message) for log in session.scalars(stmt)]


def test_sync_products_saves_items_snapshot_and_log(session_factory, monkeypatch):
    item = product_item()
    monkeypatch.setattr(sync_service, "get_products", lambda: [item])
    before = max_log_id(session_factory)

    assert sync_service.sync_products(session_factory) == 1

    with session_factory() as session:
        (product,) = session.scalars(
            select(Product).where(Product.sku == item["sku"])
        )
        assert product.price == 1500.0
        assert product.commission_rate == 0.18  # стартовая комиссия — FBS

        (snapshot,) = session.scalars(
            select(StockSnapshot).where(StockSnapshot.sku == item["sku"])
        )
        # срез делается на дату данных Ozon, а не на «сегодня»
        assert snapshot.snapshot_date == date(2026, 11, 1)
        assert snapshot.quantity_fbo == 12

    assert logs_after(session_factory, before) == [
        (
            "OZON_API",
            "success",
            "Загружено товаров: 1. Данные Ozon от 2026-11-01T10:00:00",
        )
    ]


def test_sync_products_keeps_manual_economics(session_factory, monkeypatch):
    sku = unique_sku()
    monkeypatch.setattr(
        sync_service, "get_products", lambda: [product_item(sku=sku)]
    )
    sync_service.sync_products(session_factory)

    with session_factory() as session:
        ProductRepository(session).update_economics(sku, 700.0, 0.1, 50.0, 30.0, 20.0)
        session.commit()

    # повторная синхронизация с новой ценой не затирает экономику
    monkeypatch.setattr(
        sync_service,
        "get_products",
        lambda: [product_item(sku=sku, price=2000.0, quantity_fbo=99)],
    )
    sync_service.sync_products(session_factory)

    with session_factory() as session:
        (product,) = session.scalars(select(Product).where(Product.sku == sku))
        assert product.price == 2000.0
        assert product.quantity_fbo == 99
        assert product.cost_price == 700.0
        assert product.commission_rate == 0.1
        assert product.logistics_cost == 50.0
        assert product.last_mile_cost == 30.0
        assert product.packaging_cost == 20.0


def test_sync_products_updates_same_day_snapshot(session_factory, monkeypatch):
    sku = unique_sku()
    monkeypatch.setattr(
        sync_service, "get_products", lambda: [product_item(sku=sku, quantity_fbo=10)]
    )
    sync_service.sync_products(session_factory)
    monkeypatch.setattr(
        sync_service, "get_products", lambda: [product_item(sku=sku, quantity_fbo=7)]
    )
    sync_service.sync_products(session_factory)

    with session_factory() as session:
        snapshots = session.scalars(
            select(StockSnapshot).where(StockSnapshot.sku == sku)
        ).all()
        assert len(snapshots) == 1
        assert snapshots[0].quantity_fbo == 7


def test_sync_products_empty_response_logs_warning(session_factory, monkeypatch):
    monkeypatch.setattr(sync_service, "get_products", lambda: [])
    before = max_log_id(session_factory)

    assert sync_service.sync_products(session_factory) == 0

    assert logs_after(session_factory, before) == [
        ("OZON_API", "warning", "Ozon вернул пустой список товаров")
    ]


def test_sync_products_propagates_api_error(session_factory, monkeypatch):
    def boom():
        raise OzonApiError("нет ключей")

    monkeypatch.setattr(sync_service, "get_products", boom)
    before = max_log_id(session_factory)

    with pytest.raises(OzonApiError):
        sync_service.sync_products(session_factory)

    # упавшая синхронизация не пишет в журнал успехов — новых записей нет
    assert logs_after(session_factory, before) == []


def test_sync_sales_saves_rows_and_log(session_factory, monkeypatch):
    sku = unique_sku()
    rows = [
        {"sku": sku, "day": "2026-11-01", "ordered_units": 5, "revenue": 1000.0},
        {"sku": sku, "day": "2026-11-02", "ordered_units": 3, "revenue": 600.0},
    ]
    monkeypatch.setattr(sync_service, "get_sales", lambda days: rows if days == 30 else [])
    before = max_log_id(session_factory)

    assert sync_service.sync_sales(30, session_factory) == 2

    with session_factory() as session:
        saved = session.scalars(select(SalesDaily).where(SalesDaily.sku == sku)).all()
        assert len(saved) == 2
        assert all(row.synced_at is not None for row in saved)

    assert logs_after(session_factory, before) == [
        ("OZON_ANALYTICS", "success", "Загружено строк продаж: 2")
    ]


def test_sync_sales_empty_response_logs_warning(session_factory, monkeypatch):
    monkeypatch.setattr(sync_service, "get_sales", lambda days: [])
    before = max_log_id(session_factory)

    assert sync_service.sync_sales(30, session_factory) == 0

    assert logs_after(session_factory, before) == [
        ("OZON_ANALYTICS", "warning", "Ozon вернул пустую статистику продаж")
    ]


def test_sync_products_busy_raises_honest_error(session_factory, monkeypatch):
    monkeypatch.setattr(sync_service, "SYNC_LOCK_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(
        sync_service, "get_products", lambda: pytest.fail("не должно дойти до API")
    )

    with sync_service._sync_lock:
        with pytest.raises(sync_service.SyncInProgressError, match="уже выполняется"):
            sync_service.sync_products(session_factory)


def test_sync_sales_busy_raises_honest_error(session_factory, monkeypatch):
    monkeypatch.setattr(sync_service, "SYNC_LOCK_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(
        sync_service, "get_sales", lambda days: pytest.fail("не должно дойти до API")
    )

    with sync_service._sync_lock:
        with pytest.raises(sync_service.SyncInProgressError, match="уже выполняется"):
            sync_service.sync_sales(30, session_factory)


def test_sync_lock_released_after_sync(session_factory, monkeypatch):
    monkeypatch.setattr(sync_service, "get_products", lambda: [])

    assert sync_service.sync_products(session_factory) == 0
    assert not sync_service._sync_lock.locked()


def test_sync_lock_released_after_api_error(session_factory, monkeypatch):
    def boom():
        raise OzonApiError("Ozon недоступен")

    monkeypatch.setattr(sync_service, "get_products", boom)

    with pytest.raises(OzonApiError):
        sync_service.sync_products(session_factory)

    assert not sync_service._sync_lock.locked()
