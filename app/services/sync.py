"""Синхронизация товаров и продаж с Ozon в PostgreSQL.

Перенос app/sync.py на новый стек (Фаза 2): логика та же, но данные
пишутся через репозитории, и товары + срез остатков + запись журнала
сохраняются одной транзакцией (unit of work).

Старый app/sync.py работает на SQLite и остаётся для дашборда до Фазы 4.
"""

import threading
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.database import session_scope
from app.repositories import (
    ProductRepository,
    SalesRepository,
    StockSnapshotRepository,
    SyncLogRepository,
)
from app.services.ozon_client import OzonApiError, get_products, get_sales

SessionFactory = Callable[[], Session]


class SyncInProgressError(OzonApiError):
    """Другая синхронизация уже выполняется — новые запросы к Ozon не отправляем"""


# Одна синхронизация на процесс: ручные запуски и автосинхронизация не должны
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


def sync_products(session_factory: SessionFactory | None = None) -> int:
    """Загружает товары из Ozon и сохраняет их в PostgreSQL.

    Одна транзакция: карточки товаров, дневной срез остатков и запись
    журнала синхронизации. Ошибки API пробрасывает вызывающему, чтобы
    тот записал их в журнал. Возвращает количество товаров (0, если Ozon
    вернул пустой список).
    """
    _acquire_sync_lock()
    try:
        items = get_products()
        with session_scope(session_factory) as session:
            if not items:
                SyncLogRepository(session).add(
                    "OZON_API", "warning", "Ozon вернул пустой список товаров"
                )
                return 0

            _save_products(session, items)
            return len(items)
    finally:
        _sync_lock.release()


def _save_products(session: Session, items: list[dict]) -> None:
    """Сохраняет товары, срез остатков и запись журнала одной транзакцией.

    Повторяет поведение старого app.database.save_products: экономика,
    введённая вручную, не затирается, новым товарам подставляется комиссия
    Ozon, срез за тот же день обновляется, а не дублируется.
    """
    now = datetime.now()
    ozon_updated_at = items[0].get("ozon_updated_at") or now.isoformat()

    ProductRepository(session).upsert_products(items)
    StockSnapshotRepository(session).upsert_many(
        StockSnapshotRepository.rows_from_products(items, now)
    )
    SyncLogRepository(session).add(
        "OZON_API",
        "success",
        f"Загружено товаров: {len(items)}. Данные Ozon от {ozon_updated_at}",
    )


def sync_sales(days: int = 30, session_factory: SessionFactory | None = None) -> int:
    """Загружает ежедневные продажи из Ozon и сохраняет их в PostgreSQL.

    Ошибки API пробрасывает вызывающему. Возвращает число сохраненных строк.
    Пустой ответ Ozon честно пишем в журнал как предупреждение.
    """
    _acquire_sync_lock()
    try:
        items = get_sales(days)
        with session_scope(session_factory) as session:
            if not items:
                SyncLogRepository(session).add(
                    "OZON_ANALYTICS", "warning", "Ozon вернул пустую статистику продаж"
                )
                return 0

            saved = SalesRepository(session).upsert_daily(items)
            SyncLogRepository(session).add(
                "OZON_ANALYTICS", "success", f"Загружено строк продаж: {saved}"
            )
            return saved
    finally:
        _sync_lock.release()
