"""Фоновая синхронизация Ozon в PostgreSQL (Фаза 2).

Запуск отдельным процессом:
    python -m app.services.scheduler

Интервал задаётся в .env:
    JARVIS_SYNC_INTERVAL_HOURS=6

Старый app/scheduler.py продолжает работать на SQLite для дашборда
до Фазы 4; этот модуль — тот же цикл, но поверх сервисов и новой БД.
"""

import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session

from app.core.database import Base, get_engine, session_scope
from app.repositories import SyncLogRepository
from app.services.ozon_client import OzonApiError
from app.services.sync import SyncInProgressError, sync_products, sync_sales

logger = logging.getLogger(__name__)

DEFAULT_SYNC_INTERVAL_HOURS = 6.0
SALES_SYNC_DAYS = 30
# Первый фоновый запуск внутри приложения — через минуту после старта
BACKGROUND_FIRST_RUN_DELAY_SECONDS = 60

SessionFactory = Callable[[], Session]


def interval_hours_from_env() -> float:
    """Читает интервал из JARVIS_SYNC_INTERVAL_HOURS. Некорректное значение — 6 часов."""
    raw = os.getenv("JARVIS_SYNC_INTERVAL_HOURS", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SYNC_INTERVAL_HOURS
    if value <= 0:
        return DEFAULT_SYNC_INTERVAL_HOURS
    return value


def ensure_schema() -> None:
    """Создаёт недостающие таблицы PostgreSQL (как init_db в старом слое)."""
    import app.models  # noqa: F401 — регистрируем модели в Base.metadata

    Base.metadata.create_all(get_engine())


def run_sync(session_factory: SessionFactory | None = None) -> dict:
    """Один цикл синхронизации: товары, затем продажи.

    Ошибки не роняют планировщик: пишем их в журнал и возвращаем
    в результате.
    """
    result = {"products": 0, "sales": 0, "errors": []}

    try:
        result["products"] = sync_products(session_factory)
        logger.info("Синхронизация товаров: %s", result["products"])
    except SyncInProgressError as e:
        # Ручная синхронизация уже работает — это не ошибка, пишем
        # предупреждение, чтобы не пугать красным баннером
        message = f"Автосинхронизация товаров: {e}"
        _log_sync("OZON_API", "warning", message, session_factory)
        result["errors"].append(message)
        logger.warning(message)
    except OzonApiError as e:
        message = f"Автосинхронизация товаров: {e}"
        _log_sync("OZON_API", "error", message, session_factory)
        result["errors"].append(message)
        logger.error(message)

    try:
        result["sales"] = sync_sales(SALES_SYNC_DAYS, session_factory)
        logger.info("Синхронизация продаж: %s строк", result["sales"])
    except SyncInProgressError as e:
        message = f"Автосинхронизация продаж: {e}"
        _log_sync("OZON_ANALYTICS", "warning", message, session_factory)
        result["errors"].append(message)
        logger.warning(message)
    except OzonApiError as e:
        message = f"Автосинхронизация продаж: {e}"
        _log_sync("OZON_ANALYTICS", "error", message, session_factory)
        result["errors"].append(message)
        logger.error(message)

    return result


def _log_sync(
    source: str, status: str, message: str, session_factory: SessionFactory | None
) -> None:
    """Пишет ошибку цикла в журнал новой БД, не роняя планировщик."""
    try:
        with session_scope(session_factory) as session:
            SyncLogRepository(session).add(source, status, message)
    except Exception:
        # Журналирование не должно ломать цикл синхронизации
        logger.exception("Не удалось записать ошибку синхронизации в журнал")


def create_background_scheduler(
    interval_hours: float | None = None,
    first_run_delay_seconds: int = BACKGROUND_FIRST_RUN_DELAY_SECONDS,
) -> BackgroundScheduler:
    """Фоновый планировщик для запуска внутри другого процесса."""
    interval = interval_hours if interval_hours is not None else interval_hours_from_env()
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_sync,
        "interval",
        hours=interval,
        id="ozon_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        next_run_time=datetime.now() + timedelta(seconds=first_run_delay_seconds),
    )
    return scheduler


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    ensure_schema()
    interval = interval_hours_from_env()
    print(
        f"Планировщик OZON JARVIS (PostgreSQL) запущен: синхронизация каждые "
        f"{interval:g} ч. Ctrl+C — остановить."
    )

    # Первый цикл сразу, дальше — по интервалу
    run_sync()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_sync,
        "interval",
        hours=interval,
        id="ozon_sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Планировщик остановлен.")


if __name__ == "__main__":
    main()
