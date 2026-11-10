"""Справочник складов: человекочитаемые статусы, типы и ссылки на карту."""

import math

SELLER_WAREHOUSE_STATUS_LABELS = {
    "active": "🟢 работает",
    "disabled": "🔴 не принимает",
    "blocked": "🔴 заблокирован",
    "paused": "🟡 на паузе",
}

SELLER_WAREHOUSE_TYPE_LABELS = {
    "fbs": "FBS",
    "rfbs": "rFBS",
    "fbo": "FBO",
}

OZON_WAREHOUSE_TYPE_LABELS = {
    "WAREHOUSE_TYPE_FULL_FILLMENT": "🏭 РФЦ (фулфилмент)",
    "WAREHOUSE_TYPE_ORDERS_RECEIVING_POINT": "📦 Пункт приёма (АППЗ)",
    "WAREHOUSE_TYPE_CROSS_DOCK": "🔀 Кросс-докинг",
}


def seller_status_label(status) -> str:
    """Статус моего склада: работает / не принимает / на паузе."""
    raw = str(status or "").strip().lower()
    return SELLER_WAREHOUSE_STATUS_LABELS.get(raw, raw or "—")


def warehouse_type_label(warehouse_type, source: str = "ozon") -> str:
    """Тип склада: FBS/rFBS для своих, РФЦ/АППЗ для складов Ozon."""
    raw = str(warehouse_type or "").strip()
    if source == "seller":
        return SELLER_WAREHOUSE_TYPE_LABELS.get(raw.lower(), raw or "—")
    return OZON_WAREHOUSE_TYPE_LABELS.get(raw, raw or "—")


def warehouse_map_url(latitude, longitude) -> str:
    """Ссылка на Яндекс.Карты по координатам. Пустая строка, если координат нет."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return ""
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return ""
    return f"https://yandex.ru/maps/?ll={lon},{lat}&z=15"
