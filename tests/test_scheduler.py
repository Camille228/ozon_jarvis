import pytest

from app import scheduler
from app.ozon_client import OzonApiError
from app.sync import SyncInProgressError


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


def test_run_sync_success(monkeypatch):
    monkeypatch.setattr(scheduler, "init_db", lambda: None)
    monkeypatch.setattr(scheduler, "sync_products", lambda: 13)
    monkeypatch.setattr(scheduler, "sync_sales", lambda days=30: 573)

    assert scheduler.run_sync() == {"products": 13, "sales": 573, "errors": []}


def test_run_sync_writes_errors_to_journal(monkeypatch):
    logged = []

    def boom_products():
        raise OzonApiError("товары недоступны")

    def boom_sales(days=30):
        raise OzonApiError("продажи недоступны")

    monkeypatch.setattr(scheduler, "init_db", lambda: None)
    monkeypatch.setattr(scheduler, "sync_products", boom_products)
    monkeypatch.setattr(scheduler, "sync_sales", boom_sales)
    monkeypatch.setattr(
        scheduler,
        "log_sync",
        lambda source, status, message: logged.append((source, status, message)),
    )

    result = scheduler.run_sync()

    assert result["products"] == 0
    assert result["sales"] == 0
    assert len(result["errors"]) == 2
    assert [entry[0] for entry in logged] == ["OZON_API", "OZON_ANALYTICS"]
    assert all(entry[1] == "error" for entry in logged)


def test_run_sync_collision_with_manual_sync_is_warning(monkeypatch):
    logged = []

    def busy_sales(days=30):
        raise SyncInProgressError(
            "Синхронизация уже выполняется — дождитесь её завершения"
        )

    monkeypatch.setattr(scheduler, "init_db", lambda: None)
    monkeypatch.setattr(scheduler, "sync_products", lambda: 5)
    monkeypatch.setattr(scheduler, "sync_sales", busy_sales)
    monkeypatch.setattr(
        scheduler,
        "log_sync",
        lambda source, status, message: logged.append((source, status, message)),
    )

    result = scheduler.run_sync()

    # Товары успели синхронизироваться, продажи столкнулись с ручным запуском
    assert result["products"] == 5
    assert result["sales"] == 0
    assert len(result["errors"]) == 1
    assert logged[0][0] == "OZON_ANALYTICS"
    # Коллизия — не ошибка, а предупреждение: красный баннер не нужен
    assert logged[0][1] == "warning"
    assert "уже выполняется" in logged[0][2]


def test_create_background_scheduler_uses_env_interval(monkeypatch):
    monkeypatch.setenv("JARVIS_SYNC_INTERVAL_HOURS", "3")

    sched = scheduler.create_background_scheduler(first_run_delay_seconds=0)
    jobs = sched.get_jobs()

    assert len(jobs) == 1
    assert jobs[0].trigger.interval.total_seconds() == 3 * 3600


def test_create_background_scheduler_accepts_explicit_interval():
    sched = scheduler.create_background_scheduler(
        interval_hours=1.5, first_run_delay_seconds=0
    )

    assert sched.get_jobs()[0].trigger.interval.total_seconds() == 1.5 * 3600
