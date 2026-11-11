import sqlite3

import pytest

from app.tax import (
    AUSN_INCOME_LIMIT,
    OBJECT_INCOME,
    OBJECT_INCOME_EXPENSE,
    ausn_limit_warning,
    calc_ausn,
    compare_ausn,
)


def fetch_one(db, query, params=()):
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query, params).fetchone()
    finally:
        conn.close()


# --- РАСЧЁТ НАЛОГА ---


def test_calc_ausn_income_object():
    result = calc_ausn(100_000, 40_000, OBJECT_INCOME)
    assert result.object_type == OBJECT_INCOME
    assert result.income == 100_000
    assert result.expenses == 40_000
    assert result.base == 100_000
    assert result.tax == pytest.approx(8_000)
    assert result.min_tax == 0
    assert result.tax_to_pay == pytest.approx(8_000)
    assert result.effective_rate == pytest.approx(8.0)


def test_calc_ausn_income_minus_expenses():
    result = calc_ausn(100_000, 40_000, OBJECT_INCOME_EXPENSE)
    assert result.base == pytest.approx(60_000)
    assert result.tax == pytest.approx(12_000)
    assert result.min_tax == pytest.approx(3_000)
    assert result.tax_to_pay == pytest.approx(12_000)
    assert result.effective_rate == pytest.approx(12.0)


def test_calc_ausn_min_tax_replaces_regular():
    result = calc_ausn(100_000, 95_000, OBJECT_INCOME_EXPENSE)
    assert result.tax == pytest.approx(1_000)
    assert result.min_tax == pytest.approx(3_000)
    assert result.tax_to_pay == pytest.approx(3_000)


def test_calc_ausn_expenses_above_income():
    result = calc_ausn(100_000, 150_000, OBJECT_INCOME_EXPENSE)
    assert result.base == 0
    assert result.tax == 0
    # Уплачивается минимальный налог 3% от доходов
    assert result.tax_to_pay == pytest.approx(3_000)


def test_calc_ausn_defaults_to_income_object():
    result = calc_ausn(50_000)
    assert result.object_type == OBJECT_INCOME
    assert result.expenses == 0


def test_calc_ausn_bad_amounts_become_zero():
    result = calc_ausn("abc", None)
    assert result.income == 0
    assert result.expenses == 0
    assert result.tax_to_pay == 0
    assert result.effective_rate == 0


def test_calc_ausn_negative_amounts_become_zero():
    result = calc_ausn(-100, -50, OBJECT_INCOME_EXPENSE)
    assert result.income == 0
    assert result.expenses == 0
    assert result.tax_to_pay == 0


def test_calc_ausn_unknown_object_raises():
    with pytest.raises(ValueError):
        calc_ausn(100, 50, "patent")


def test_compare_ausn_returns_both_objects():
    results = compare_ausn(100_000, 40_000)
    assert [r.object_type for r in results] == [OBJECT_INCOME, OBJECT_INCOME_EXPENSE]
    assert results[0].tax_to_pay == pytest.approx(8_000)
    assert results[1].tax_to_pay == pytest.approx(12_000)


def test_limit_warning_below_limit():
    assert ausn_limit_warning(AUSN_INCOME_LIMIT) is None


def test_limit_warning_above_limit():
    warning = ausn_limit_warning(AUSN_INCOME_LIMIT + 1)
    assert warning is not None
    assert "превышают лимит" in warning
    assert "60,000,000" in warning


def test_limit_warning_bad_input():
    assert ausn_limit_warning(None) is None
    assert ausn_limit_warning("не число") is None


# --- ОПЕРАЦИИ В БАЗЕ ---


def test_init_db_creates_tax_operations_table(db):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert "tax_operations" in tables


def test_add_tax_operation_roundtrip(db):
    op_id = db.add_tax_operation("2026-02-01", "income", 150_000.50, "Поступление от Ozon")

    row = fetch_one(db, "SELECT * FROM tax_operations WHERE id = ?", (op_id,))
    assert row["operation_date"] == "2026-02-01"
    assert row["kind"] == "income"
    assert row["amount"] == pytest.approx(150_000.50)
    assert row["description"] == "Поступление от Ozon"
    assert row["created_at"]


def test_add_tax_operation_trims_description(db):
    db.add_tax_operation("2026-02-01", "expense", 100, "  реклама  ")

    row = fetch_one(db, "SELECT description FROM tax_operations")
    assert row["description"] == "реклама"


def test_add_tax_operation_accepts_full_iso_datetime(db):
    db.add_tax_operation("2026-01-15T12:34:56", "income", 100)

    row = fetch_one(db, "SELECT operation_date FROM tax_operations")
    assert row["operation_date"] == "2026-01-15"


def test_tax_operations_period_filter_includes_bounds(db):
    db.add_tax_operation("2026-01-15", "income", 1000)
    db.add_tax_operation("2026-02-10", "income", 2000)
    db.add_tax_operation("2026-03-05", "expense", 500)

    assert len(db.tax_operations("2026-01-01", "2026-12-31")) == 3
    # Обе границы включаются
    in_period = db.tax_operations("2026-02-10", "2026-03-05")
    assert {row["amount"] for row in in_period} == {2000, 500}
    assert len(db.tax_operations("2026-01-01", "2026-01-31")) == 1


def test_tax_operations_without_filter_newest_first(db):
    db.add_tax_operation("2026-01-15", "income", 1000)
    db.add_tax_operation("2026-02-10", "expense", 2000)

    rows = db.tax_operations()
    assert [row["amount"] for row in rows] == [2000, 1000]


def test_add_tax_operation_invalid_kind(db):
    with pytest.raises(ValueError):
        db.add_tax_operation("2026-01-01", "transfer", 100)


@pytest.mark.parametrize("bad_amount", [0, -10, "abc", None])
def test_add_tax_operation_invalid_amount(db, bad_amount):
    with pytest.raises(ValueError):
        db.add_tax_operation("2026-01-01", "income", bad_amount)


def test_add_tax_operation_bad_date(db):
    with pytest.raises(ValueError):
        db.add_tax_operation("01.02.2026", "income", 100)
    with pytest.raises(ValueError):
        db.add_tax_operation("", "income", 100)


def test_delete_tax_operation(db):
    op_id = db.add_tax_operation("2026-01-15", "income", 1000)
    db.delete_tax_operation(op_id)

    assert db.tax_operations() == []
