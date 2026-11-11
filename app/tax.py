"""Калькулятор АУСН: налоговая база и налог по двум объектам налогообложения.

Ставки АУСН (глава 26.5 НК РФ, эксперимент с 2022 года):
- объект «доходы» — 8%;
- объект «доходы минус расходы» — 20%, но не меньше минимального налога 3%
  от доходов.

На АУСН страховые взносы не уплачиваются (кроме взносов на травматизм),
поэтому в расходах их не учитываем. Точную сумму налога считает ФНС по
банковским данным — здесь предварительная оценка для сверки.
"""

from dataclasses import dataclass

AUSN_INCOME_RATE = 0.08
AUSN_INCOME_EXPENSE_RATE = 0.20
AUSN_MIN_TAX_RATE = 0.03

# Лимит доходов для применения АУСН, рублей в год
AUSN_INCOME_LIMIT = 60_000_000

OBJECT_INCOME = "income"
OBJECT_INCOME_EXPENSE = "income_minus_expenses"

OBJECT_LABELS = {
    OBJECT_INCOME: "Доходы — 8%",
    OBJECT_INCOME_EXPENSE: "Доходы минус расходы — 20%",
}

TAX_OPERATION_KINDS = ("income", "expense")
TAX_OPERATION_KIND_LABELS = {
    "income": "➕ доход",
    "expense": "➖ расход",
}


@dataclass
class AusnResult:
    """Результат расчёта налога по одному объекту налогообложения."""

    object_type: str
    income: float
    expenses: float
    base: float
    rate: float
    tax: float
    min_tax: float
    tax_to_pay: float
    effective_rate: float


def _amount(value) -> float:
    """Приводит сумму к неотрицательному числу."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number < 0:  # NaN или отрицательное
        return 0.0
    return number


def calc_ausn(income, expenses=0, object_type: str = OBJECT_INCOME) -> AusnResult:
    """Считает налог АУСН по выбранному объекту.

    income — доходы за период, expenses — расходы (для объекта
    «доходы минус расходы»). Суммы — в рублях.
    """
    if object_type not in OBJECT_LABELS:
        raise ValueError(f"Неизвестный объект налогообложения: {object_type!r}")

    income = _amount(income)
    expenses = _amount(expenses)

    if object_type == OBJECT_INCOME:
        base = income
        rate = AUSN_INCOME_RATE
        tax = base * rate
        min_tax = 0.0
        tax_to_pay = tax
    else:
        base = max(0.0, income - expenses)
        rate = AUSN_INCOME_EXPENSE_RATE
        tax = base * rate
        min_tax = income * AUSN_MIN_TAX_RATE
        tax_to_pay = max(tax, min_tax)

    effective_rate = tax_to_pay / income * 100 if income > 0 else 0.0

    return AusnResult(
        object_type=object_type,
        income=income,
        expenses=expenses,
        base=base,
        rate=rate,
        tax=tax,
        min_tax=min_tax,
        tax_to_pay=tax_to_pay,
        effective_rate=effective_rate,
    )


def compare_ausn(income, expenses=0) -> list[AusnResult]:
    """Считает налог по обоим объектам, чтобы выбрать выгодный."""
    return [
        calc_ausn(income, expenses, OBJECT_INCOME),
        calc_ausn(income, expenses, OBJECT_INCOME_EXPENSE),
    ]


def ausn_limit_warning(income) -> str | None:
    """Предупреждение, если доходы превышают лимит применения АУСН."""
    income = _amount(income)
    if income > AUSN_INCOME_LIMIT:
        return (
            f"Доходы {income:,.2f} ₽ превышают лимит АУСН "
            f"{AUSN_INCOME_LIMIT:,.0f} ₽ — право на АУСН теряется."
        )
    return None
