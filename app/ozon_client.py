import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from app.config import OZON_CLIENT_ID, OZON_API_KEY

BASE_URL = "https://api-seller.ozon.ru"
PLACEHOLDERS = {None, "", "ваш_числовой_client_id", "ваш_api_key"}

# Сколько товаров запрашивать за одну страницу / один запрос
PAGE_SIZE = 1000
INFO_LIST_CHUNK_SIZE = 500

# Статистика продаж (Ozon Analytics API)
SALES_URL = "/v1/analytics/data"
SALES_METRICS = ["ordered_units", "revenue"]
SALES_DIMENSIONS = ["day", "sku"]
SALES_PAGE_SIZE = 1000
# Ozon ограничивает частоту запросов аналитики — между страницами делаем паузу
SALES_PAGE_PAUSE_SECONDS = 1.0
SALES_MAX_DAYS = 90

# Временные сбои Ozon: лимит запросов (429) и ошибки сервера (500/502/503)
RETRYABLE_STATUSES = {429, 500, 502, 503}


class OzonApiError(Exception):
    """Ошибка обращения к Ozon Seller API"""


class OzonKeysMissing(OzonApiError):
    """Ключи Ozon не заданы в файле .env"""


def _check_keys():
    if OZON_CLIENT_ID in PLACEHOLDERS or OZON_API_KEY in PLACEHOLDERS:
        raise OzonKeysMissing(
            "Ключи Ozon не заданы. Заполните OZON_CLIENT_ID и OZON_API_KEY в файле .env"
        )


def _build_client(transport=None) -> httpx.Client:
    _check_keys()
    return httpx.Client(
        base_url=BASE_URL,
        headers={
            "Client-Id": OZON_CLIENT_ID,
            "Api-Key": OZON_API_KEY,
            "Content-Type": "application/json",
        },
        timeout=30,
        transport=transport,
    )


def _server_time(response: httpx.Response):
    """Честная метка времени: часы сервера Ozon (заголовок Date), а не наши локальные"""
    raw = response.headers.get("Date")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _request(
    client: httpx.Client, url: str, payload: dict, max_retries: int = 5
) -> httpx.Response:
    """POST-запрос к Ozon с повторными попытками.

    Временные сбои (429, 500, 502, 503) пережидаем с экспоненциальной
    задержкой 2, 4, 8, 16 сек и повторяем запрос. Остальные ошибки
    (например, 401 — неверный ключ) не повторяются и падают сразу.
    """
    for attempt in range(1, max_retries + 1):
        response = client.post(url, json=payload)

        if response.status_code == 200:
            return response

        # Временный сбой, и попытки еще остались — ждем и повторяем
        if response.status_code in RETRYABLE_STATUSES and attempt < max_retries:
            wait_seconds = 2 ** attempt
            print(f"Лимит Ozon! Ждем {wait_seconds} сек...")
            time.sleep(wait_seconds)
            continue

        # Ошибка не временная (401 и т.п.) или попытки исчерпаны
        try:
            details = response.json()
        except ValueError:
            details = response.text[:500]
        raise OzonApiError(
            f"Ozon API {url} вернул HTTP {response.status_code}: {details}"
        )

    # Сюда попадаем, только если max_retries <= 0
    raise OzonApiError(f"Ozon API {url}: не сделано ни одной попытки (max_retries={max_retries})")


def _extract_page(data: dict):
    """Достает (items, token следующей страницы) из ответа Ozon.

    Поддерживает два реальных формата ответа:
    - v3 (product/list): items и last_id внутри "result"
    - v4/v5 (stocks, prices): items и cursor на верхнем уровне
    """
    result = data.get("result")
    if isinstance(result, dict):
        items = result.get("items") or []
        token = result.get("last_id") or ""
    else:
        items = data.get("items") or []
        token = data.get("cursor") or ""
    return items, token


def _paginate(client: httpx.Client, url: str, base_payload: dict, token_key: str = "last_id"):
    """Проходит все страницы ответа Ozon, возвращает (items, первый ответ).

    token_key — имя параметра пагинации в запросе:
    "last_id" (v3 product/list) или "cursor" (v4 stocks, v5 prices).
    Стоп: пустая страница, токен не изменился или страница меньше лимита.
    """
    items = []
    token = ""
    first_response = None
    while True:
        payload = dict(base_payload)
        payload[token_key] = token
        payload["limit"] = PAGE_SIZE
        response = _request(client, url, payload)
        if first_response is None:
            first_response = response
        batch, new_token = _extract_page(response.json())
        items.extend(batch)
        if not batch or not new_token or len(batch) < PAGE_SIZE or new_token == token:
            break
        token = new_token
    return items, first_response


def _paginate_offset(
    client: httpx.Client,
    url: str,
    base_payload: dict,
    page_size: int = SALES_PAGE_SIZE,
    pause_seconds: float = SALES_PAGE_PAUSE_SECONDS,
):
    """Проходит страницы через offset/limit (аналитика продаж Ozon)."""
    items = []
    offset = 0
    while True:
        payload = dict(base_payload)
        payload["limit"] = page_size
        payload["offset"] = offset
        response = _request(client, url, payload)
        result = response.json().get("result") or {}
        batch = result.get("data") or []
        items.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
        if pause_seconds:
            time.sleep(pause_seconds)
    return items


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_sales_row(row: dict):
    """Разбирает строку ответа аналитики: dimensions=[день, SKU], metrics=[шт., выручка]."""
    dimensions = row.get("dimensions") or []
    metrics = row.get("metrics") or []
    day = None
    sku = None
    for dimension in dimensions:
        dim_id = str(dimension.get("id") or "")
        if _DAY_RE.match(dim_id):
            day = dim_id
        elif dim_id:
            sku = dim_id
    if not day or not sku:
        return None

    ordered = _parse_number(metrics[0]) if len(metrics) > 0 else 0
    revenue = _parse_number(metrics[1]) if len(metrics) > 1 else 0
    return {
        "sku": sku,
        "day": day,
        "ordered_units": int(ordered or 0),
        "revenue": float(revenue or 0),
    }


def _fetch_sales(client: httpx.Client, days: int):
    """Шаг 5: ежедневные продажи по каждому SKU за период."""
    date_to = datetime.now().date()
    date_from = date_to - timedelta(days=days - 1)
    rows = _paginate_offset(
        client,
        SALES_URL,
        {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "metrics": SALES_METRICS,
            "dimension": SALES_DIMENSIONS,
            "filters": [],
        },
    )
    sales = []
    for row in rows:
        parsed = _parse_sales_row(row)
        if parsed:
            sales.append(parsed)
    return sales


def _parse_number(value):
    """Ozon присылает цены строками в разных форматах: '990.00', '1 200,50'"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _first_number(block: dict, *keys):
    """Достает первое числовое значение по одному из возможных названий поля"""
    for key in keys:
        value = _parse_number(block.get(key))
        if value is not None:
            return value
    return None


def _extract_sku(item: dict) -> str:
    sku = item.get("sku")
    if sku:
        return str(sku)
    for source in item.get("sources") or []:
        if source.get("sku"):
            return str(source["sku"])
    product_id = item.get("id") or item.get("product_id")
    return str(product_id) if product_id else ""


def _chunks(values: list, size: int):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _fetch_product_list(client: httpx.Client):
    """Шаг 1: список всех товаров (product_id + offer_id)"""
    items, first_response = _paginate(
        client, "/v3/product/list", {"filter": {"visibility": "ALL"}}
    )
    product_ids = [item["product_id"] for item in items if item.get("product_id")]
    return product_ids, first_response


def _fetch_info_list(client: httpx.Client, product_ids: list):
    """Шаг 2: названия и SKU товаров по их product_id.

    Эндпоинт не пагинируется и возвращает items на верхнем уровне (без "result").
    """
    records = []
    for chunk in _chunks(product_ids, INFO_LIST_CHUNK_SIZE):
        response = _request(client, "/v3/product/info/list", {"product_id": chunk})
        for item in response.json().get("items", []):
            sku = _extract_sku(item)
            if not sku:
                continue
            records.append({
                "product_id": item.get("id") or item.get("product_id"),
                "sku": sku,
                "offer_id": str(item.get("offer_id") or ""),
                "name": item.get("name") or "Без названия",
            })
    return records


def _fetch_stocks(client: httpx.Client):
    """Шаг 3: остатки на складах (FBO/FBS) по всем товарам (пагинация cursor)"""
    items, _ = _paginate(
        client,
        "/v4/product/info/stocks",
        {"filter": {"visibility": "ALL"}},
        token_key="cursor",
    )
    stocks = {}
    for item in items:
        product_id = str(item.get("product_id"))
        fbo = fbs = 0
        for entry in item.get("stocks") or []:
            stock_type = str(entry.get("type") or "").lower()
            present = int(entry.get("present") or 0)
            if stock_type == "fbo":
                fbo += present
            elif stock_type == "fbs":
                fbs += present
        stocks[product_id] = {"fbo": fbo, "fbs": fbs}
    return stocks


def _fetch_prices(client: httpx.Client):
    """Шаг 4: цены и комиссии Ozon по всем товарам (пагинация cursor)"""
    items, _ = _paginate(
        client,
        "/v5/product/info/prices",
        {"filter": {"visibility": "ALL"}},
        token_key="cursor",
    )
    prices = {}
    for item in items:
        product_id = str(item.get("product_id"))
        price_block = item.get("price") or {}
        commissions = item.get("commissions") or {}
        price = _first_number(price_block, "price", "marketing_price", "min_price", "old_price")
        commission_fbo = _first_number(
            commissions, "fbo_percent", "sales_percent_fbo", "fbo_delivered_to_customer_percent"
        )
        commission_fbs = _first_number(
            commissions, "fbs_percent", "sales_percent_fbs", "fbs_delivered_to_customer_percent"
        )
        if commissions and commission_fbo is None and commission_fbs is None:
            print(f"⚠️ Ozon прислал комиссии в неожиданном формате: {list(commissions)[:10]}")
        prices[product_id] = {"price": price, "fbo": commission_fbo, "fbs": commission_fbs}
    return prices


def get_products(client: httpx.Client | None = None):
    """Собирает товары с ценами, остатками и комиссиями через реальный Ozon Seller API.

    Возвращает список словарей:
    sku, offer_id, name, price, quantity_fbo, quantity_fbs,
    commission_rate_ozon_fbo, commission_rate_ozon_fbs, ozon_updated_at.
    """
    own_client = client is None
    if own_client:
        client = _build_client()
    try:
        product_ids, first_response = _fetch_product_list(client)
        if not product_ids:
            return []

        # Честная метка времени: когда Ozon реально отдал данные
        ozon_time = _server_time(first_response) or datetime.now()

        records = _fetch_info_list(client, product_ids)
        stocks = _fetch_stocks(client)
        prices = _fetch_prices(client)
    finally:
        if own_client:
            client.close()

    products = []
    for record in records:
        product_id = str(record["product_id"])
        price_info = prices.get(product_id, {})
        stock_info = stocks.get(product_id, {})
        products.append({
            "sku": record["sku"],
            "offer_id": record["offer_id"],
            "name": record["name"],
            "price": price_info.get("price") or 0.0,
            "quantity_fbo": stock_info.get("fbo", 0),
            "quantity_fbs": stock_info.get("fbs", 0),
            "commission_rate_ozon_fbo": price_info.get("fbo"),
            "commission_rate_ozon_fbs": price_info.get("fbs"),
            "ozon_updated_at": ozon_time.isoformat(),
        })
    return products


def get_sales(days: int = 30, client: httpx.Client | None = None):
    """Возвращает ежедневные продажи по SKU за последние `days` дней.

    Каждая запись: sku, day (YYYY-MM-DD), ordered_units, revenue.
    Ozon отдает статистику максимум за 3 месяца, поэтому days <= 90.
    """
    if days < 1:
        raise ValueError("days должен быть >= 1")
    if days > SALES_MAX_DAYS:
        raise ValueError(f"Ozon отдает статистику максимум за {SALES_MAX_DAYS} дней")

    own_client = client is None
    if own_client:
        client = _build_client()
    try:
        return _fetch_sales(client, days)
    finally:
        if own_client:
            client.close()
