import json

import httpx
import pytest

from app.ozon_client import (
    BASE_URL,
    WAREHOUSE_PAGE_SIZE,
    OzonApiError,
    _extract_page,
    _extract_sku,
    _paginate_offset,
    _parse_number,
    _parse_ozon_warehouse,
    _parse_sales_row,
    _parse_seller_warehouse,
    _request,
    _server_time,
    get_products,
    get_sales,
    get_seller_warehouses,
    search_ozon_warehouses,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("990.00", 990.0),
        ("1 200,50", 1200.5),
        ("1\u00a0200.50", 1200.5),
        (300, 300.0),
        (None, None),
        ("", None),
        ("нет цены", None),
        (True, None),
    ],
)
def test_parse_number(raw, expected):
    assert _parse_number(raw) == expected


def test_extract_page_supports_v3_and_cursor_formats():
    v3 = {"result": {"items": [{"id": 1}], "last_id": "next"}}
    assert _extract_page(v3) == ([{"id": 1}], "next")

    cursor = {"items": [{"product_id": 1}], "cursor": "abc"}
    assert _extract_page(cursor) == ([{"product_id": 1}], "abc")

    assert _extract_page({}) == ([], "")


def test_extract_sku_prefers_sku_then_sources_then_product_id():
    assert _extract_sku({"sku": 111}) == "111"
    assert _extract_sku({"sources": [{"sku": 222}]}) == "222"
    assert _extract_sku({"id": 333}) == "333"
    assert _extract_sku({}) == ""


def test_server_time_uses_ozon_date_header():
    response = httpx.Response(
        200, headers={"Date": "Tue, 10 Nov 2026 20:00:00 GMT"}
    )

    result = _server_time(response)

    assert result is not None
    assert result.tzinfo is None
    assert (result.year, result.month, result.day) == (2026, 11, 10)


def test_request_does_not_retry_client_errors():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(401, json={"message": "bad key"})

    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    with pytest.raises(OzonApiError, match="HTTP 401"):
        _request(client, "/v1/test", {})
    assert calls == ["/v1/test"]


def test_request_retries_temporary_errors(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, json={"message": "too many requests"})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("app.ozon_client.time.sleep", lambda seconds: None)
    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))

    response = _request(client, "/v1/test", {})

    assert response.status_code == 200
    assert len(calls) == 2


def test_get_products_merges_list_info_stocks_and_prices():
    def handler(request):
        path = request.url.path
        if path == "/v3/product/list":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "items": [{"product_id": 111}, {"product_id": 222}],
                        "last_id": "",
                    }
                },
                headers={"Date": "Tue, 10 Nov 2026 20:00:00 GMT"},
            )
        if path == "/v3/product/info/list":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": 111, "sku": 1001, "offer_id": "A", "name": "Товар 1"},
                        {"id": 222, "sku": 1002, "offer_id": "B", "name": "Товар 2"},
                    ]
                },
            )
        if path == "/v4/product/info/stocks":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "product_id": 111,
                            "stocks": [
                                {"type": "fbo", "present": 5},
                                {"type": "fbs", "present": 2},
                            ],
                        },
                        {"product_id": 222, "stocks": [{"type": "fbo", "present": 0}]},
                    ],
                    "cursor": "",
                },
            )
        if path == "/v5/product/info/prices":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "product_id": 111,
                            "price": {"price": "1 200,50"},
                            "commissions": {"fbo_percent": 23, "fbs_percent": 24},
                        },
                        {
                            "product_id": 222,
                            "price": {"price": "300"},
                            "commissions": {"fbo_percent": 23},
                        },
                    ],
                    "cursor": "",
                },
            )
        raise AssertionError(f"Неожиданный путь: {path}")

    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))

    products = get_products(client=client)

    assert [p["sku"] for p in products] == ["1001", "1002"]

    first, second = products
    assert first["name"] == "Товар 1"
    assert first["price"] == pytest.approx(1200.5)
    assert first["quantity_fbo"] == 5
    assert first["quantity_fbs"] == 2
    assert first["commission_rate_ozon_fbo"] == 23.0
    assert first["commission_rate_ozon_fbs"] == 24.0
    assert first["ozon_updated_at"].startswith("2026-11-10")

    assert second["price"] == pytest.approx(300.0)
    assert second["quantity_fbo"] == 0
    assert second["quantity_fbs"] == 0
    assert second["commission_rate_ozon_fbs"] is None


def test_parse_sales_row_reads_day_sku_and_metrics():
    row = {
        "dimensions": [
            {"id": "2026-11-10", "name": ""},
            {"id": "111", "name": "Товар"},
        ],
        "metrics": [3, 2990.5],
    }

    assert _parse_sales_row(row) == {
        "sku": "111",
        "day": "2026-11-10",
        "ordered_units": 3,
        "revenue": 2990.5,
    }


def test_parse_sales_row_does_not_depend_on_dimension_order():
    row = {
        "dimensions": [
            {"id": "111", "name": "Товар"},
            {"id": "2026-11-10", "name": ""},
        ],
        "metrics": [1, 100],
    }

    parsed = _parse_sales_row(row)

    assert parsed["sku"] == "111"
    assert parsed["day"] == "2026-11-10"


def test_parse_sales_row_skips_incomplete_rows():
    assert _parse_sales_row({"dimensions": [], "metrics": [1, 2]}) is None
    assert _parse_sales_row({"dimensions": [{"id": "111"}], "metrics": []}) is None


def test_paginate_offset_walks_pages(monkeypatch):
    pages = {
        0: [{"n": 1}, {"n": 2}],
        2: [{"n": 3}],
    }
    offsets = []

    def handler(request):
        payload = json.loads(request.content)
        offsets.append(payload["offset"])
        return httpx.Response(200, json={"result": {"data": pages[payload["offset"]]}})

    monkeypatch.setattr("app.ozon_client.time.sleep", lambda seconds: None)
    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))

    items = _paginate_offset(client, "/v1/analytics/data", {}, page_size=2)

    assert [item["n"] for item in items] == [1, 2, 3]
    assert offsets == [0, 2]


def test_get_sales_sends_expected_payload_and_parses_rows():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "result": {
                    "data": [
                        {
                            "dimensions": [
                                {"id": "2026-11-10", "name": ""},
                                {"id": "111", "name": "Товар"},
                            ],
                            "metrics": [3, 2990.5],
                        }
                    ]
                }
            },
        )

    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))

    sales = get_sales(days=30, client=client)

    assert sales == [
        {
            "sku": "111",
            "day": "2026-11-10",
            "ordered_units": 3,
            "revenue": 2990.5,
        }
    ]
    assert captured["metrics"] == ["ordered_units", "revenue"]
    assert captured["dimension"] == ["day", "sku"]
    assert captured["limit"] == 1000
    assert captured["offset"] == 0


@pytest.mark.parametrize("days", [0, -1, 91])
def test_get_sales_validates_period(days):
    with pytest.raises(ValueError):
        get_sales(days=days)


def test_parse_seller_warehouse_reads_status_and_address():
    parsed = _parse_seller_warehouse(
        {
            "warehouse_id": 1020005023494460,
            "name": "казань",
            "warehouse_type": "fbs",
            "status": "disabled",
            "is_rfbs": False,
            "pause_at": None,
            "address_info": {
                "address": "Россия, Казань, Чистопольская улица, 19а",
                "latitude": 55.81987,
                "longitude": 49.114884,
            },
        }
    )

    assert parsed == {
        "warehouse_id": "1020005023494460",
        "name": "казань",
        "address": "Россия, Казань, Чистопольская улица, 19а",
        "warehouse_type": "fbs",
        "status": "disabled",
        "is_rfbs": False,
        "latitude": 55.81987,
        "longitude": 49.114884,
        "pause_at": None,
    }


def test_parse_ozon_warehouse_does_not_invent_status():
    parsed = _parse_ozon_warehouse(
        {
            "warehouse_id": 15431806189000,
            "warehouse_type": "WAREHOUSE_TYPE_FULL_FILLMENT",
            "coordinates": {"latitude": 56.127205, "longitude": 37.211363},
            "address": "Россия, Московская область, Хоругвино",
            "name": "ХОРУГВИНО_РФЦ",
        }
    )

    assert parsed["warehouse_id"] == "15431806189000"
    assert parsed["name"] == "ХОРУГВИНО_РФЦ"
    assert parsed["warehouse_type"] == "WAREHOUSE_TYPE_FULL_FILLMENT"
    assert parsed["latitude"] == 56.127205
    assert parsed["longitude"] == 37.211363
    # Ozon не отдаёт статус приёма в этом методе — не выдумываем его
    assert parsed["status"] == ""


def test_get_seller_warehouses_walks_pages(monkeypatch):
    offsets = []
    full_page = [
        {"warehouse_id": index, "name": f"Склад {index}"}
        for index in range(WAREHOUSE_PAGE_SIZE)
    ]

    def handler(request):
        payload = json.loads(request.content)
        offsets.append(payload["offset"])
        if payload["offset"] == 0:
            return httpx.Response(200, json={"warehouses": full_page})
        return httpx.Response(
            200, json={"warehouses": [{"warehouse_id": 999, "name": "Последний"}]}
        )

    monkeypatch.setattr("app.ozon_client.time.sleep", lambda seconds: None)
    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))

    warehouses = get_seller_warehouses(client=client)

    assert len(warehouses) == WAREHOUSE_PAGE_SIZE + 1
    assert warehouses[-1]["warehouse_id"] == "999"
    assert offsets == [0, WAREHOUSE_PAGE_SIZE]


def test_search_ozon_warehouses_sends_expected_payload():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "search": [
                    {
                        "warehouse_id": 15431806189000,
                        "warehouse_type": "WAREHOUSE_TYPE_FULL_FILLMENT",
                        "coordinates": {"latitude": 56.127205, "longitude": 37.211363},
                        "address": "Россия, Московская область, Хоругвино",
                        "name": "ХОРУГВИНО_РФЦ",
                    }
                ]
            },
        )

    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))

    warehouses = search_ozon_warehouses("Хоругвино", client=client)

    assert captured["filter_by_supply_type"] == [1, 2]
    assert captured["search"] == "Хоругвино"
    assert [item["name"] for item in warehouses] == ["ХОРУГВИНО_РФЦ"]


def test_search_ozon_warehouses_requires_four_characters():
    with pytest.raises(ValueError, match="минимум 4"):
        search_ozon_warehouses("abc")
