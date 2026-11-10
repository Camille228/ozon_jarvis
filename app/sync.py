"""Общая логика синхронизации: дашборд, CLI и планировщик используют одно и то же."""

from app.database import log_sync, save_products, save_sales
from app.ozon_client import get_products, get_sales


def sync_products() -> int:
    """Загружает товары из Ozon и сохраняет их.

    Ошибки API пробрасывает вызывающему, чтобы тот записал их в журнал.
    Возвращает количество товаров (0, если Ozon вернул пустой список).
    """
    products = get_products()
    if not products:
        log_sync("OZON_API", "warning", "Ozon вернул пустой список товаров")
        return 0
    save_products(products)
    return len(products)


def sync_sales(days: int = 30) -> int:
    """Загружает ежедневные продажи из Ozon и сохраняет их.

    Ошибки API пробрасывает вызывающему. Возвращает число сохраненных строк.
    """
    sales = get_sales(days)
    return save_sales(sales)
