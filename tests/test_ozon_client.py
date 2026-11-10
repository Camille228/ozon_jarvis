import httpx
import pytest

from app.ozon_client import (
    BASE_URL,
    OzonApiError,
    _extract_page,
    _extract_sku,
    _parse_number,
    _request,
    _server_time,
    get_products,
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
