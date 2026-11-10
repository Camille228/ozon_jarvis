"""Планировщик автоматической синхронизации Ozon.

Запуск отдельным процессом:
    python -m app.scheduler

Интервал задается в .env:
    JARVIS_SYNC_INTERVAL_HOURS=6

Либо можно включить фоновую синхронизацию внутри дашборда:
    JARVIS_AUTOSYNC=1
"""

import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler

from app.database import init_db, log_sync
from app.ozon_client import OzonApiError
from app.sync import sync_products, sync_sales

logger = logging.getLogger(__name__)

DEFAULT_SYNC_INTERVAL_HOURS = 6.0
SALES_SYNC_DAYS = 30
# Первый фоновый запуск внутри дашборда — через минуту после старта
BACKGROUND_FIRST_RUN_DELAY_SECONDS = 60


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


def run_sync() -> dict:
    """Один цикл синхронизации: товары, затем продажи.

    Ошибки не роняют планировщик: пишем их в журнал и возвращаем в результате.
    """
    init_db()
    result = {"products": 0, "sales": 0, "errors": []}

    try:
        result["products"] = sync_products()
        logger.info("Синхронизация товаров: %s", result["products"])
    except OzonApiError as e:
        message = f"Автосинхронизация товаров: {e}"
        log_sync("OZON_API", "error", message)
        result["errors"].append(message)
        logger.error(message)

    try:
        result["sales"] = sync_sales(SALES_SYNC_DAYS)
        logger.info("Синхронизация продаж: %s строк", result["sales"])
    except OzonApiError as e:
        message = f"Автосинхронизация продаж: {e}"
        log_sync("OZON_ANALYTICS", "error", message)
        result["errors"].append(message)
        logger.error(message)

    return result


def create_background_scheduler(
    interval_hours: float | None = None,
    first_run_delay_seconds: int = BACKGROUND_FIRST_RUN_DELAY_SECONDS,
) -> BackgroundScheduler:
    """Фоновый планировщик для запуска внутри другого процесса (Streamlit)."""
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
    init_db()
    interval = interval_hours_from_env()
    print(
        f"Планировщик OZON JARVIS запущен: синхронизация каждые {interval:g} ч. "
        "Ctrl+C — остановить."
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
