"""Журнал рекомендаций: что предложили → что сделали → какой результат."""

# Статусы записей. Открытые — те, по которым еще нет решения.
RECOMMENDATION_STATUSES = ("new", "in_progress", "done", "rejected")
OPEN_RECOMMENDATION_STATUSES = ("new", "in_progress")

RECOMMENDATION_STATUS_LABELS = {
    "new": "🆕 предложено",
    "in_progress": "🟡 в работе",
    "done": "🟢 выполнено",
    "rejected": "⚪ отклонено",
}
STATUS_BY_LABEL = {
    label: status for status, label in RECOMMENDATION_STATUS_LABELS.items()
}

# Типы рекомендаций, которые система предлагает сама
RECOMMENDATION_KIND_LABELS = {
    "manual": "✍️ вручную",
    "low_stock": "📦 заканчивается остаток",
    "stale_stock": "🧊 зависший остаток",
    "no_sales": "💤 нет продаж",
}


def _number(value) -> float:
    """Безопасно приводит значение из pandas/Excel к числу."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_stock_recommendations(rows) -> list[dict]:
    """Строит рекомендации по текущим остаткам.

    rows — записи с полями sku, name, stock_total, avg_daily, days_left,
    status, is_stale (например, stock.to_dict("records")).
    Возвращает список словарей sku/kind/problem для добавления в журнал.
    """
    recommendations = []
    for row in rows:
        sku = str(row.get("sku") or "").strip()
        if not sku:
            continue

        stock = _number(row.get("stock_total"))
        if stock <= 0:
            continue

        avg_daily = _number(row.get("avg_daily"))
        days_left = row.get("days_left")
        name = str(row.get("name") or f"SKU {sku}")

        if row.get("is_stale"):
            if avg_daily <= 0:
                kind = "no_sales"
                problem = (
                    f"{name}: нет продаж, на складе {stock:.0f} шт. — проверить "
                    "карточку и цену или вывести товар из ассортимента."
                )
            else:
                kind = "stale_stock"
                problem = (
                    f"{name}: запаса хватит на {_number(days_left):.0f} дн. — "
                    "снизить цену, запустить акцию или не заказывать новую партию."
                )
        elif row.get("status") in ("critical", "warning"):
            kind = "low_stock"
            problem = (
                f"{name}: остатка хватит на {_number(days_left):.1f} дн. — "
                "спланировать поставку, чтобы не уйти в out-of-stock."
            )
        else:
            continue

        recommendations.append({"sku": sku, "kind": kind, "problem": problem})

    return recommendations
