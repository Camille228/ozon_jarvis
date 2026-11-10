"""Единый расчет юнит-экономики товара.

Один источник правды для дашборда и консольного вывода, чтобы формулы
не расходились между разными частями приложения.
"""

from dataclasses import dataclass


def to_float(value, default: float = 0.0) -> float:
    """Аккуратно приводит значение из БД/формы к float.

    None, пустая строка и NaN (из pandas) превращаются в default.
    """
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    # NaN не равен сам себе
    if result != result:
        return default
    return result


@dataclass(frozen=True)
class UnitEconomics:
    """Предварительная юнит-экономика одного товара."""

    price: float = 0.0
    cost_price: float = 0.0
    commission_rate: float = 0.0
    logistics_cost: float = 0.0
    last_mile_cost: float = 0.0
    packaging_cost: float = 0.0
    ad_rate: float = 0.0

    @classmethod
    def build(
        cls,
        price=0.0,
        cost_price=0.0,
        commission_rate=0.0,
        logistics_cost=0.0,
        last_mile_cost=0.0,
        packaging_cost=0.0,
        ad_rate=0.0,
    ) -> "UnitEconomics":
        return cls(
            price=to_float(price),
            cost_price=to_float(cost_price),
            commission_rate=to_float(commission_rate),
            logistics_cost=to_float(logistics_cost),
            last_mile_cost=to_float(last_mile_cost),
            packaging_cost=to_float(packaging_cost),
            ad_rate=to_float(ad_rate),
        )

    @property
    def commission(self) -> float:
        """Комиссия Ozon в рублях."""
        return self.price * self.commission_rate / 100

    @property
    def ad_cost(self) -> float:
        """Расход на рекламу (ДРР) в рублях."""
        return self.price * self.ad_rate / 100

    @property
    def total_costs(self) -> float:
        """Все расходы, кроме закупки товара."""
        return (
            self.commission
            + self.logistics_cost
            + self.last_mile_cost
            + self.packaging_cost
            + self.ad_cost
        )

    @property
    def profit(self) -> float:
        """Предварительная прибыль с одной штуки."""
        return self.price - self.cost_price - self.total_costs

    @property
    def margin(self) -> float:
        """Маржа в процентах от цены. 0, если цена не задана."""
        if not self.price:
            return 0.0
        return self.profit / self.price * 100
