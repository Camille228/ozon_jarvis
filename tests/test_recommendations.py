from app.recommendations import (
    RECOMMENDATION_STATUS_LABELS,
    RECOMMENDATION_STATUSES,
    STATUS_BY_LABEL,
    build_stock_recommendations,
)


def test_every_status_has_a_label_and_reverse_mapping():
    assert set(RECOMMENDATION_STATUS_LABELS) == set(RECOMMENDATION_STATUSES)
    for status, label in RECOMMENDATION_STATUS_LABELS.items():
        assert STATUS_BY_LABEL[label] == status


def test_build_stock_recommendations_by_problem_type():
    rows = [
        {
            "sku": "111",
            "name": "Заканчивается",
            "stock_total": 10,
            "avg_daily": 2,
            "days_left": 5,
            "status": "critical",
            "is_stale": False,
        },
        {
            "sku": "222",
            "name": "Зависший",
            "stock_total": 100,
            "avg_daily": 1,
            "days_left": 100,
            "status": "ok",
            "is_stale": True,
        },
        {
            "sku": "333",
            "name": "Без продаж",
            "stock_total": 5,
            "avg_daily": 0,
            "days_left": None,
            "status": "no_sales",
            "is_stale": True,
        },
        {
            "sku": "444",
            "name": "Нет на складе",
            "stock_total": 0,
            "avg_daily": 0,
            "days_left": 0,
            "status": "out",
            "is_stale": False,
        },
        {
            "sku": "555",
            "name": "Норма",
            "stock_total": 10,
            "avg_daily": 1,
            "days_left": 10,
            "status": "ok",
            "is_stale": False,
        },
    ]

    recommendations = build_stock_recommendations(rows)

    assert [item["kind"] for item in recommendations] == [
        "low_stock",
        "stale_stock",
        "no_sales",
    ]
    assert [item["sku"] for item in recommendations] == ["111", "222", "333"]
    assert "5.0 дн." in recommendations[0]["problem"]
    assert "100 дн." in recommendations[1]["problem"]
    assert "нет продаж" in recommendations[2]["problem"]


def test_build_stock_recommendations_skips_rows_without_sku():
    rows = [
        {
            "sku": "",
            "name": "Без SKU",
            "stock_total": 10,
            "avg_daily": 2,
            "days_left": 5,
            "status": "critical",
            "is_stale": False,
        }
    ]

    assert build_stock_recommendations(rows) == []
