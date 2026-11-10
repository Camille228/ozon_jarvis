"""Расчёт остатков и продаж — один источник правды для разделов дашборда."""

from dataclasses import dataclass, field

import pandas as pd

from app.inventory import (
    STOCK_STATUS_LABELS,
    average_daily_sales,
    days_of_stock,
    is_stale,
    stock_status,
)


@dataclass
class StockTable:
    """Результат расчёта остатков за выбранный период."""

    stock: pd.DataFrame
    recent: pd.DataFrame
    period_start: pd.Timestamp
    period_end: pd.Timestamp
    divisor: int
    unmatched_skus: set = field(default_factory=set)
    last_sales_sync: pd.Timestamp | None = None


def build_stock_table(products: pd.DataFrame, sales: pd.DataFrame, period_days: int):
    """Строит таблицу остатков с продажами за последние `period_days` дней.

    Период считается от последнего дня, за который есть данные: Ozon может
    отдавать статистику с задержкой. Возвращает None, если корректных дат
    или продаж за период нет.
    """
    prepared = sales.copy()
    prepared["day"] = pd.to_datetime(prepared["day"], errors="coerce")
    prepared["ordered_units"] = pd.to_numeric(
        prepared["ordered_units"], errors="coerce"
    ).fillna(0)
    prepared["revenue"] = pd.to_numeric(prepared["revenue"], errors="coerce").fillna(0)

    period_end = prepared["day"].max()
    if pd.isna(period_end):
        return None

    period_start = period_end - pd.Timedelta(days=period_days - 1)
    recent = prepared[(prepared["day"] >= period_start) & (prepared["day"] <= period_end)]
    if recent.empty:
        return None

    observed_days = int((recent["day"].max() - recent["day"].min()).days) + 1
    divisor = max(1, min(period_days, observed_days))

    agg = recent.groupby("sku", as_index=False).agg(
        sold=("ordered_units", "sum"),
        revenue=("revenue", "sum"),
    )
    agg["avg_daily"] = [average_daily_sales(units, divisor) for units in agg["sold"]]

    stock = products[["sku", "name", "price", "quantity_fbo", "quantity_fbs"]].copy()
    stock["price"] = pd.to_numeric(stock["price"], errors="coerce").fillna(0)
    stock["quantity_fbo"] = pd.to_numeric(
        stock["quantity_fbo"], errors="coerce"
    ).fillna(0)
    stock["quantity_fbs"] = pd.to_numeric(
        stock["quantity_fbs"], errors="coerce"
    ).fillna(0)
    stock["stock_total"] = stock["quantity_fbo"] + stock["quantity_fbs"]
    stock = stock.merge(agg, on="sku", how="left")
    stock[["sold", "revenue", "avg_daily"]] = stock[
        ["sold", "revenue", "avg_daily"]
    ].fillna(0)

    stock["days_left"] = [
        days_of_stock(total, avg)
        for total, avg in zip(stock["stock_total"], stock["avg_daily"])
    ]
    stock["status"] = [
        stock_status(days, total)
        for days, total in zip(stock["days_left"], stock["stock_total"])
    ]
    stock["status_label"] = stock["status"].map(STOCK_STATUS_LABELS)
    stock["days_left_display"] = [
        "—" if pd.isna(days) else f"{days:.1f}" for days in stock["days_left"]
    ]
    stock["is_stale"] = [
        is_stale(total, avg)
        for total, avg in zip(stock["stock_total"], stock["avg_daily"])
    ]

    stock["_sort_days"] = [
        float("inf") if pd.isna(days) else days for days in stock["days_left"]
    ]
    stock = stock.sort_values("_sort_days").drop(columns="_sort_days")

    last_sync = None
    if "synced_at" in sales:
        last_sync = pd.to_datetime(sales["synced_at"], errors="coerce").max()
        if pd.isna(last_sync):
            last_sync = None

    return StockTable(
        stock=stock,
        recent=recent,
        period_start=period_start,
        period_end=period_end,
        divisor=divisor,
        unmatched_skus=set(recent["sku"]) - set(products["sku"]),
        last_sales_sync=last_sync,
    )
