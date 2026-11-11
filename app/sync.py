"""Общая логика синхронизации: дашборд, CLI и планировщик используют одно и то же."""

import threading

from app.database import log_sync, save_products, save_sales
from app.services.ozon_client import OzonApiError, get_products, get_sales


class SyncInProgressError(OzonApiError):
    """Другая синхронизация уже выполняется — новые запросы к Ozon не отправляем"""


# Одна синхронизация на процесс: ручные клики и автосинхронизация не должны
# одновременно стучаться в Ozon — так мы не дублируем запросы и не сталкиваемся
# в лимитах (HTTP 429)
_sync_lock = threading.Lock()
SYNC_LOCK_TIMEOUT_SECONDS = 2.0


def _acquire_sync_lock() -> None:
    """Занимает блокировку синхронизации или честно объясняет, почему нельзя.

    Ждем SYNC_LOCK_TIMEOUT_SECONDS, пока текущая синхронизация освободит
    очередь. Не дождались — поднимаем ошибку, а не молча грузим API повторно.
    """
    if _sync_lock.acquire(timeout=SYNC_LOCK_TIMEOUT_SECONDS):
        return
    raise SyncInProgressError(
        "Синхронизация уже выполняется (вероятно, автосинхронизация) — "
        "дождитесь её завершения и попробуйте снова"
    )


def sync_products() -> int:
    """Загружает товары из Ozon и сохраняет их.

    Ошибки API пробрасывает вызывающему, чтобы тот записал их в журнал.
    Возвращает количество товаров (0, если Ozon вернул пустой список).
    """
    _acquire_sync_lock()
    try:
        products = get_products()
        if not products:
            log_sync("OZON_API", "warning", "Ozon вернул пустой список товаров")
            return 0
        save_products(products)
        return len(products)
    finally:
        _sync_lock.release()


def sync_sales(days: int = 30) -> int:
    """Загружает ежедневные продажи из Ozon и сохраняет их.

    Ошибки API пробрасывает вызывающему. Возвращает число сохраненных строк.
    """
    _acquire_sync_lock()
    try:
        sales = get_sales(days)
        return save_sales(sales)
    finally:
        _sync_lock.release()
