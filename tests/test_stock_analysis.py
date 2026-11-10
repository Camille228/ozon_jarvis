import pandas as pd

from app.stock_analysis import build_stock_table


def sample_products():
    return pd.DataFrame(
        [
            {
                "sku": "111",
                "name": "Быстрый товар",
                "price": 1000.0,
                "quantity_fbo": 10,
                "quantity_fbs": 0,
            },
            {
                "sku": "222",
                "name": "Медленный товар",
                "price": 500.0,
                "quantity_fbo": 100,
                "quantity_fbs": 0,
            },
            {
                "sku": "333",
                "name": "Без продаж",
                "price": 100.0,
                "quantity_fbo": 5,
                "quantity_fbs": 0,
            },
        ]
    )


def sample_sales():
    rows = []
    for day in pd.date_range("2026-11-01", periods=10, freq="D"):
        rows.append(
            {
                "sku": "111",
                "day": day.strftime("%Y-%m-%d"),
                "ordered_units": 2,
                "revenue": 2000.0,
                "synced_at": "2026-11-10T10:00:00",
            }
        )
        rows.append(
            {
                "sku": "222",
                "day": day.strftime("%Y-%m-%d"),
                "ordered_units": 0,
                "revenue": 0.0,
                "synced_at": "2026-11-10T10:00:00",
            }
        )
    # SKU, которого нет в активном списке товаров (например, архивный)
    rows.append(
        {
            "sku": "999",
            "day": "2026-11-10",
            "ordered_units": 1,
            "revenue": 100.0,
            "synced_at": "2026-11-10T10:00:00",
        }
    )
    return pd.DataFrame(rows)


def test_build_stock_table_calculates_period_and_statuses():
    result = build_stock_table(sample_products(), sample_sales(), period_days=30)

    assert result is not None
    assert result.period_end == pd.Timestamp("2026-11-10")
    assert result.period_start == pd.Timestamp("2026-10-12")
    assert result.divisor == 10
    assert result.unmatched_skus == {"999"}
    assert result.last_sales_sync == pd.Timestamp("2026-11-10 10:00:00")

    stock = result.stock.set_index("sku")

    fast = stock.loc["111"]
    assert fast["sold"] == 20
    assert fast["avg_daily"] == 2.0
    assert fast["days_left"] == 5.0
    assert fast["status"] == "critical"
    assert fast["is_stale"] is False or not fast["is_stale"]

    slow = stock.loc["222"]
    assert slow["avg_daily"] == 0.0
    assert pd.isna(slow["days_left"])
    assert slow["status"] == "no_sales"
    assert slow["is_stale"] is True or slow["is_stale"]

    no_sales = stock.loc["333"]
    assert no_sales["sold"] == 0
    assert no_sales["status"] == "no_sales"
    assert no_sales["is_stale"] is True or no_sales["is_stale"]


def test_build_stock_table_returns_none_without_valid_dates():
    sales = pd.DataFrame(
        [{"sku": "111", "day": "not-a-date", "ordered_units": 1, "revenue": 1}]
    )

    assert build_stock_table(sample_products(), sales, period_days=30) is None


def test_build_stock_table_returns_none_for_empty_sales():
    sales = pd.DataFrame(columns=["sku", "day", "ordered_units", "revenue"])

    assert build_stock_table(sample_products(), sales, period_days=30) is None
