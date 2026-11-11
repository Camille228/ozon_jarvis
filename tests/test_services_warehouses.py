"""Тесты сервиса синхронизации складов (Фаза 2)."""

from sqlalchemy import select

from app.models import Warehouse
from app.services import warehouses as warehouses_service
from test_repositories import unique_wh_id


def seller_warehouse(wh_id: str, **overrides) -> dict:
    item = {
        "warehouse_id": wh_id,
        "name": "Склад на Ленина",
        "address": "ул. Ленина, 1",
        "warehouse_type": "fbs",
        "status": "active",
        "is_rfbs": False,
        "latitude": 55.75,
        "longitude": 37.62,
    }
    item.update(overrides)
    return item


def warehouses_of(session_factory, source: str | None = None) -> list[Warehouse]:
    with session_factory() as session:
        stmt = select(Warehouse).order_by(Warehouse.warehouse_id)
        if source:
            stmt = stmt.where(Warehouse.source == source)
        return list(session.scalars(stmt))


def test_sync_seller_warehouses_replaces_old_rows(session_factory, monkeypatch):
    wh_id = unique_wh_id()
    monkeypatch.setattr(
        warehouses_service,
        "get_seller_warehouses",
        lambda: [seller_warehouse(wh_id)],
    )

    assert warehouses_service.sync_seller_warehouses(session_factory) == 1
    (warehouse,) = warehouses_of(session_factory, "seller")
    assert warehouse.warehouse_id == wh_id
    assert warehouse.is_rfbs is False
    assert warehouse.synced_at is not None

    # склад исчез из Ozon → replace=True вычищает устаревшую запись
    other = unique_wh_id()
    monkeypatch.setattr(
        warehouses_service,
        "get_seller_warehouses",
        lambda: [seller_warehouse(other, name="Новый склад")],
    )
    assert warehouses_service.sync_seller_warehouses(session_factory) == 1

    (warehouse,) = warehouses_of(session_factory, "seller")
    assert warehouse.warehouse_id == other
    assert warehouse.name == "Новый склад"


def test_sync_ozon_warehouses_appends_search_results(session_factory, monkeypatch):
    wh_id = unique_wh_id()
    monkeypatch.setattr(
        warehouses_service,
        "get_seller_warehouses",
        lambda: [seller_warehouse(unique_wh_id())],
    )
    warehouses_service.sync_seller_warehouses(session_factory)

    monkeypatch.setattr(
        warehouses_service,
        "search_ozon_warehouses",
        lambda query: [
            {
                "warehouse_id": wh_id,
                "name": f"РФЦ {query}",
                "warehouse_type": "WAREHOUSE_TYPE_FULL_FILLMENT",
            }
        ]
        if query == "Москва"
        else [],
    )

    assert warehouses_service.sync_ozon_warehouses("Москва", session_factory) == 1

    seller = warehouses_of(session_factory, "seller")
    ozon = warehouses_of(session_factory, "ozon")
    assert len(seller) == 1  # поиск складов Ozon не трогает мои склады
    assert len(ozon) == 1
    assert ozon[0].name == "РФЦ Москва"
    assert ozon[0].warehouse_type == "WAREHOUSE_TYPE_FULL_FILLMENT"


def warehouse_ids(session_factory) -> set[str]:
    """Id всех складов (внешняя транзакция видит и реальные данные)."""
    with session_factory() as session:
        return {wh.warehouse_id for wh in session.scalars(select(Warehouse))}


def test_sync_warehouses_empty_response_saves_nothing(session_factory, monkeypatch):
    monkeypatch.setattr(warehouses_service, "get_seller_warehouses", lambda: [])
    monkeypatch.setattr(warehouses_service, "search_ozon_warehouses", lambda query: [])

    # в реальной базе уже есть склады (миграция) — сравниваем с состоянием до теста
    before = warehouse_ids(session_factory)

    assert warehouses_service.sync_seller_warehouses(session_factory) == 0
    assert warehouses_service.sync_ozon_warehouses("Москва", session_factory) == 0

    assert warehouse_ids(session_factory) == before
