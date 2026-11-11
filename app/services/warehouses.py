"""Синхронизация справочника складов с Ozon в PostgreSQL.

Перенос логики кнопок дашборда «Обновить мои склады» и «Найти склады
Ozon» в сервисный слой (Фаза 2): сами запросы к API оставлены в
app/services/ozon_client.py, ошибки пробрасываются вызывающему —
журналировать их будет слой API (Фаза 3).
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.database import session_scope
from app.repositories import WarehouseRepository
from app.services.ozon_client import get_seller_warehouses, search_ozon_warehouses

SessionFactory = Callable[[], Session]


def sync_seller_warehouses(session_factory: SessionFactory | None = None) -> int:
    """Обновляет мои склады (FBS). Возвращает число сохраненных складов.

    Список складов продавца в Ozon считается полным, поэтому устаревшие
    записи этого источника заменяются (replace=True).
    """
    items = get_seller_warehouses()
    with session_scope(session_factory) as session:
        return WarehouseRepository(session).save(items, "seller", replace=True)


def sync_ozon_warehouses(
    query: str, session_factory: SessionFactory | None = None
) -> int:
    """Ищет склады Ozon (FBO) по названию или городу и сохраняет найденное.

    Возвращает число сохраненных складов. Найденное дополняет справочник,
    а не заменяет его: Ozon отдаёт склады порциями, набор собирается
    поисковыми запросами.
    """
    items = search_ozon_warehouses(query)
    with session_scope(session_factory) as session:
        return WarehouseRepository(session).save(items, "ozon")
