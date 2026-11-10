import pytest

from app.inventory import (
    STOCK_STATUS_LABELS,
    average_daily_sales,
    days_of_stock,
    stock_status,
)


def test_average_daily_sales():
    assert average_daily_sales(30, 30) == pytest.approx(1.0)
    assert average_daily_sales("15", "30") == pytest.approx(0.5)
    assert average_daily_sales(10, 0) == 0.0
    assert average_daily_sales(None, 30) == 0.0


def test_days_of_stock():
    assert days_of_stock(100, 10) == pytest.approx(10.0)
    assert days_of_stock(0, 10) == 0.0
    assert days_of_stock(-5, 10) == 0.0


def test_days_of_stock_without_sales_is_unknown():
    assert days_of_stock(100, 0) is None
    assert days_of_stock(100, None) is None


@pytest.mark.parametrize(
    ("days_left", "stock", "expected"),
    [
        (None, 0, "out"),
        (10, 0, "out"),
        (None, 10, "no_sales"),
        (float("nan"), 10, "no_sales"),
        (0, 10, "critical"),
        (6.9, 10, "critical"),
        (7, 10, "warning"),
        (13.9, 10, "warning"),
        (14, 10, "ok"),
        (100, 10, "ok"),
    ],
)
def test_stock_status(days_left, stock, expected):
    assert stock_status(days_left, stock) == expected


def test_every_status_has_a_label():
    for status in {"out", "critical", "warning", "ok", "no_sales"}:
        assert status in STOCK_STATUS_LABELS
