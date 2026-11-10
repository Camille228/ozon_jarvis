from app.warehouses import (
    OZON_WAREHOUSE_TYPE_LABELS,
    SELLER_WAREHOUSE_STATUS_LABELS,
    SELLER_WAREHOUSE_TYPE_LABELS,
    seller_status_label,
    warehouse_map_url,
    warehouse_type_label,
)


def test_seller_status_label_uses_known_statuses():
    assert seller_status_label("active") == SELLER_WAREHOUSE_STATUS_LABELS["active"]
    assert (
        seller_status_label("disabled")
        == SELLER_WAREHOUSE_STATUS_LABELS["disabled"]
    )
    assert seller_status_label("ACTIVE") == SELLER_WAREHOUSE_STATUS_LABELS["active"]


def test_seller_status_label_does_not_hide_unknown_status():
    assert seller_status_label("new_status") == "new_status"
    assert seller_status_label(None) == "—"


def test_warehouse_type_label_for_seller_and_ozon():
    assert warehouse_type_label("fbs", "seller") == SELLER_WAREHOUSE_TYPE_LABELS["fbs"]
    assert warehouse_type_label("FBS", "seller") == SELLER_WAREHOUSE_TYPE_LABELS["fbs"]
    assert (
        warehouse_type_label("WAREHOUSE_TYPE_FULL_FILLMENT", "ozon")
        == OZON_WAREHOUSE_TYPE_LABELS["WAREHOUSE_TYPE_FULL_FILLMENT"]
    )


def test_warehouse_type_label_keeps_unknown_type():
    assert warehouse_type_label("WAREHOUSE_TYPE_UNKNOWN", "ozon") == (
        "WAREHOUSE_TYPE_UNKNOWN"
    )
    assert warehouse_type_label("", "ozon") == "—"


def test_warehouse_map_url_builds_yandex_link():
    url = warehouse_map_url(55.81987, 49.114884)

    assert url == "https://yandex.ru/maps/?ll=49.114884,55.81987&z=15"


def test_warehouse_map_url_is_empty_without_coordinates():
    assert warehouse_map_url(None, None) == ""
    assert warehouse_map_url("нет", 49.1) == ""
    assert warehouse_map_url(float("nan"), 49.1) == ""
