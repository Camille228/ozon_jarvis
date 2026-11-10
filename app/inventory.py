"""Безопасный остаток: на сколько дней хватит товара при текущей скорости продаж."""

import math

from app.economics import to_float

# Пороги в днях: ниже 7 — критично, 7–14 — предупреждение, больше 14 — норма
LOW_STOCK_DAYS = 7.0
WARNING_STOCK_DAYS = 14.0
# Запас больше чем на 30 дней считаем медленным/зависшим
STALE_STOCK_DAYS = 30.0

STOCK_STATUS_LABELS = {
    "out": "🔴 нет в наличии",
    "critical": "🔴 < 7 дн.",
    "warning": "🟡 7–14 дн.",
    "ok": "🟢 > 14 дн.",
    "no_sales": "⚪ нет продаж",
}


def average_daily_sales(units, days) -> float:
    """Средние продажи в день. 0, если период некорректен."""
    days = to_float(days)
    if days <= 0:
        return 0.0
    return to_float(units) / days


def days_of_stock(stock, avg_daily_sales_value) -> float | None:
    """На сколько дней хватит остатка. None, если продаж нет и прогноз невозможен."""
    avg = to_float(avg_daily_sales_value)
    if avg <= 0:
        return None
    return max(0.0, to_float(stock)) / avg


def stock_status(days_left, stock=0) -> str:
    """Статус остатка: out / no_sales / critical / warning / ok."""
    if to_float(stock) <= 0:
        return "out"
    # pandas превращает None в NaN — оба варианта означают «прогноза нет»
    if days_left is None:
        return "no_sales"
    try:
        if math.isnan(days_left):
            return "no_sales"
    except TypeError:
        pass
    days_left = to_float(days_left)
    if days_left < LOW_STOCK_DAYS:
        return "critical"
    if days_left < WARNING_STOCK_DAYS:
        return "warning"
    return "ok"


def is_stale(stock, avg_daily_sales_value, threshold_days=STALE_STOCK_DAYS) -> bool:
    """Товар зависший/медленный: остаток есть, а продаж нет или запаса > 30 дней."""
    if to_float(stock) <= 0:
        return False
    coverage = days_of_stock(stock, avg_daily_sales_value)
    if coverage is None:
        return True
    return coverage > to_float(threshold_days)
