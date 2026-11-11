import pytest

from app import sync
from app.ozon_client import OzonApiError


def test_sync_products_saves_and_returns_count(monkeypatch):
    saved = []
    monkeypatch.setattr(sync, "get_products", lambda: [{"sku": "111"}])
    monkeypatch.setattr(sync, "save_products", lambda items: saved.append(items))

    assert sync.sync_products() == 1
    assert saved == [[{"sku": "111"}]]

def test_sync_products_empty_response_logs_warning(monkeypatch):
    logged = []
    monkeypatch.setattr(sync, "get_products", lambda: [])
    monkeypatch.setattr(sync, "log_sync", lambda *args: logged.append(args))

    assert sync.sync_products() == 0
    assert logged == [("OZON_API", "warning", "Ozon вернул пустой список товаров")]


def test_sync_products_propagates_api_error(monkeypatch):
    def boom():
        raise OzonApiError("нет ключей")

    monkeypatch.setattr(sync, "get_products", boom)

    with pytest.raises(OzonApiError):
        sync.sync_products()


def test_sync_sales_saves_and_returns_count(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sync,
        "get_sales",
        lambda days: [{"sku": "111", "day": "2026-11-10"}] if days == 30 else [],
    )
    monkeypatch.setattr(
        sync, "save_sales", lambda items: (calls.append(items), len(items))[1]
    )

    assert sync.sync_sales(30) == 1
    assert calls == [[{"sku": "111", "day": "2026-11-10"}]]


def test_sync_sales_busy_raises_honest_error(monkeypatch):
    monkeypatch.setattr(sync, "SYNC_LOCK_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(sync, "get_sales", lambda days: pytest.fail("не должно дойти до API"))

    with sync._sync_lock:
        with pytest.raises(sync.SyncInProgressError, match="уже выполняется"):
            sync.sync_sales(30)


def test_sync_products_busy_raises_honest_error(monkeypatch):
    monkeypatch.setattr(sync, "SYNC_LOCK_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(
        sync, "get_products", lambda: pytest.fail("не должно дойти до API")
    )

    with sync._sync_lock:
        with pytest.raises(sync.SyncInProgressError, match="уже выполняется"):
            sync.sync_products()


def test_sync_lock_released_after_sync(monkeypatch):
    monkeypatch.setattr(sync, "get_sales", lambda days: [])
    monkeypatch.setattr(sync, "save_sales", lambda items: 0)

    assert sync.sync_sales(30) == 0
    assert not sync._sync_lock.locked()


def test_sync_lock_released_after_api_error(monkeypatch):
    def boom():
        raise OzonApiError("Ozon недоступен")

    monkeypatch.setattr(sync, "get_products", boom)

    with pytest.raises(OzonApiError):
        sync.sync_products()

    assert not sync._sync_lock.locked()
