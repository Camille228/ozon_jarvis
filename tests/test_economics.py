import pytest

from app.economics import UnitEconomics, to_float


def test_to_float_handles_empty_and_invalid_values():
    assert to_float(None) == 0.0
    assert to_float("") == 0.0
    assert to_float("abc") == 0.0
    assert to_float(float("nan")) == 0.0
    assert to_float("12.5") == 12.5
    assert to_float(7) == 7.0


def test_profit_and_margin_with_all_costs():
    econ = UnitEconomics.build(
        price=1000,
        cost_price=300,
        commission_rate=20,
        logistics_cost=50,
        last_mile_cost=30,
        packaging_cost=10,
    )

    assert econ.commission == pytest.approx(200.0)
    assert econ.total_costs == pytest.approx(290.0)
    assert econ.profit == pytest.approx(410.0)
    assert econ.margin == pytest.approx(41.0)


def test_ad_rate_is_part_of_scenario_profit():
    econ = UnitEconomics.build(price=1000, ad_rate=10)

    assert econ.ad_cost == pytest.approx(100.0)
    assert econ.profit == pytest.approx(900.0)


def test_zero_price_does_not_divide_by_zero():
    econ = UnitEconomics.build(price=0, cost_price=100)

    assert econ.margin == 0.0
    assert econ.profit == pytest.approx(-100.0)


def test_empty_values_are_treated_as_zero():
    econ = UnitEconomics.build(price=None, cost_price=None, commission_rate=None)

    assert econ.price == 0.0
    assert econ.profit == 0.0
