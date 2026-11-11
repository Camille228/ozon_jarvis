"""Тесты фонового планировщика на PostgreSQL (Фаза 2)."""

import pytest

from app.services import scheduler
from app.services.ozon_client import OzonApiError
from app.services.sync import SyncInProgressError
from test_services_sync import logs_after, max_log_id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, scheduler.DEFAULT_SYNC_INTERVAL_HOURS),
        ("", scheduler.DEFAULT_SYNC_INTERVAL_HOURS),
        ("abc", scheduler.DEFAULT_SYNC_INTERVAL_HOURS),
        ("0", scheduler.DEFAULT_SYNC_INTERVAL_HOURS),
        ("-2", scheduler.DEFAULT_SYNC_INTERVAL_HOURS),
        ("1.5", 1.5),
        ("12", 12.0),
    ],
)
def test_interval_hours_from_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("JARVIS_SYNC_INTERVAL_HOURS", raising=False)
    else:
        monkeypatch.setenv("JARVIS_SYNC_INTERVAL_HOURS", raw)

    assert scheduler.interval_hours_from_env() == expected


def test_run_sync_success(session_factory, monkeypatch):
    monkeypatch.setattr(scheduler, "sync_products", lambda factory=None: 13)
    monkeypatch.setattr(scheduler, "sync_sales", lambda days=30, factory=None: 573)

    assert scheduler.run_sync(session_factory) == {
        "products": 13,
        "sales": 573,
        "errors": [],
    }


def test_run_sync_logs_api_error_to_sync_log(session_factory, monkeypatch):
    def boom(factory=None):
        raise OzonApiError("Ozon недоступен")

    monkeypatch.setattr(scheduler, "sync_products", boom)
    monkeypatch.setattr(scheduler, "sync_sales", lambda days=30, factory=None: 573)
    before = max_log_id(session_factory)

    result = scheduler.run_sync(session_factory)

    expected_errors = ["Автосинхронизация товаров: Ozon недоступен"]
    assert result == {"products": 0, "sales": 573, "errors": expected_errors}
    # ошибка цикла попадает в журнал синхронизации
    assert logs_after(session_factory, before) == [
        ("OZON_API", "error", "Автосинхронизация товаров: Ozon недоступен")
    ]


def test_run_sync_sync_in_progress_is_warning(session_factory, monkeypatch):
    def busy(factory=None):
        raise SyncInProgressError("Синхронизация уже выполняется")

    monkeypatch.setattr(scheduler, "sync_products", busy)
    monkeypatch.setattr(scheduler, "sync_sales", lambda days=30, factory=None: 573)
    before = max_log_id(session_factory)

    result = scheduler.run_sync(session_factory)

    assert result["errors"] == [
        "Автосинхронизация товаров: Синхронизация уже выполняется"
    ]
    assert logs_after(session_factory, before) == [
        (
            "OZON_API",
            "warning",
            "Автосинхронизация товаров: Синхронизация уже выполняется",
        )
    ]


def test_run_sync_survives_log_failure(monkeypatch):
    """Ошибка журналирования не должна ронять цикл синхронизации."""

    def boom(factory=None):
        raise OzonApiError("Ozon упал")

    def broken_factory():
        raise RuntimeError("база ушла на обед")

    monkeypatch.setattr(scheduler, "sync_products", boom)
    monkeypatch.setattr(scheduler, "sync_sales", lambda days=30, factory=None: 10)

    # журнал пишет в недоступную базу — цикл всё равно доходит до продаж
    result = scheduler.run_sync(broken_factory)

    assert result == {
        "products": 0,
        "sales": 10,
        "errors": ["Автосинхронизация товаров: Ozon упал"],
    }
