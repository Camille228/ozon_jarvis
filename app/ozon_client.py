import os
import re
import threading
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
# Сколько всего попыток: паузы между повторами растут — 2, 4, 8, 16, 32 сек
MAX_RETRIES = 6
# Верхняя граница паузы между повторами, сек (даже если Retry-After больше)
MAX_RETRY_WAIT_SECONDS = 60.0

DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 1.2


def _min_request_interval_from_env() -> float:
    """Минимальная пауза между запросами из JARVIS_OZON_MIN_REQUEST_INTERVAL.

    Некорректное или отрицательное значение — берем default (1.2 сек).
    """
    try:
        value = float(os.getenv("JARVIS_OZON_MIN_REQUEST_INTERVAL", ""))
    except (TypeError, ValueError):
        return DEFAULT_MIN_REQUEST_INTERVAL_SECONDS
    if value < 0:
        return DEFAULT_MIN_REQUEST_INTERVAL_SECONDS
    return value


# Минимальный интервал между запросами к Ozon (одна очередь на весь процесс):
# держимся ниже лимита «запросов в секунду», чтобы не ловить HTTP 429
MIN_REQUEST_INTERVAL_SECONDS = _min_request_interval_from_env()

_throttle_lock = threading.Lock()
_last_request_monotonic: float | None = None


def _throttle():
    """Выдерживает MIN_REQUEST_INTERVAL_SECONDS между любыми запросами к Ozon.

    Один общий таймер на процесс: кнопки дашборда и фоновый планировщик
    встают в очередь, а не бьют в API одновременно.
    """
    global _last_request_monotonic
    with _throttle_lock:
        now = time.monotonic()
        if _last_request_monotonic is not None:
            wait_seconds = MIN_REQUEST_INTERVAL_SECONDS - (
                now - _last_request_monotonic
            )
            if wait_seconds > 0:
                time.sleep(wait_seconds)
                now = time.monotonic()
        _last_request_monotonic = now

# Склады продавца (FBS) и склады Ozon (FBO)
WAREHOUSE_LIST_URL = "/v2/warehouse/list"
WAREHOUSE_PAGE_SIZE = 200  # Ozon не принимает limit больше 200
OZON_WAREHOUSE_SEARCH_URL = "/v1/warehouse/fbo/list"
OZON_WAREHOUSE_SEARCH_MIN_LENGTH = 4
# Ozon принимает только числовые значения enum: 1 и 2 — доступные типы поставок
OZON_WAREHOUSE_SUPPLY_TYPES = [1, 2]


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


def _retry_wait_seconds(response: httpx.Response, attempt: int) -> float:
    """Сколько ждать перед повтором: совет сервера или экспоненциальная пауза.

    При 429 Ozon может прислать заголовок Retry-After (секунды) — слушаем его,
    иначе пауза растет экспоненциально: 2, 4, 8, 16, 32 сек.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            header_seconds = float(retry_after)
        except ValueError:
            header_seconds = 0.0
    else:
        header_seconds = 0.0
    return min(MAX_RETRY_WAIT_SECONDS, max(header_seconds, float(2 ** attempt)))


def _request(
    client: httpx.Client, url: str, payload: dict, max_retries: int = MAX_RETRIES
) -> httpx.Response:
    """POST-запрос к Ozon с троттлингом и повторными попытками.

    Перед каждым запросом выдерживаем MIN_REQUEST_INTERVAL_SECONDS — так не
    попадаем в лимит Ozon «запросов в секунду». Временные сбои (429, 500,
    502, 503) пережидаем и повторяем. Остальные ошибки (например, 401 —
    неверный ключ) не повторяются и падают сразу.
    """
    for attempt in range(1, max_retries + 1):
        _throttle()
        response = client.post(url, json=payload)

        if response.status_code == 200:
            return response

        # Временный сбой, и попытки еще остались — ждем и повторяем
        if response.status_code in RETRYABLE_STATUSES and attempt < max_retries:
            wait_seconds = _retry_wait_seconds(response, attempt)
            print(
                f"Ozon ответил HTTP {response.status_code} — ждем "
                f"{wait_seconds:.0f} сек и повторяем "
                f"(попытка {attempt + 1} из {max_retries})..."
            )
            time.sleep(wait_seconds)
            continue

        # Ошибка не временная (401 и т.п.) или попытки исчерпаны
        try:
            details = response.json()
        except ValueError:
            details = response.text[:500]
        message = f"Ozon API {url} вернул HTTP {response.status_code}: {details}"
        if response.status_code in RETRYABLE_STATUSES:
            message += (
                f" — переждали {max_retries - 1} пауз, лимит все еще превышен. "
                "Подождите минуту и попробуйте снова"
            )
        raise OzonApiError(message)

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


def _parse_seller_warehouse(row: dict) -> dict:
    """Разбирает склад продавца из /v2/warehouse/list."""
    address_info = row.get("address_info") or {}
    return {
        "warehouse_id": str(row.get("warehouse_id") or ""),
        "name": row.get("name") or "",
        "address": address_info.get("address") or "",
        "warehouse_type": row.get("warehouse_type") or "",
        "status": row.get("status") or "",
        "is_rfbs": bool(row.get("is_rfbs")),
        "latitude": address_info.get("latitude"),
        "longitude": address_info.get("longitude"),
        "pause_at": row.get("pause_at"),
    }


def _parse_ozon_warehouse(row: dict) -> dict:
    """Разбирает склад Ozon из /v1/warehouse/fbo/list.

    Ozon в этом методе не отдаёт статус приёма поставок — только тип,
    адрес и координаты. Не придумываем статус, оставляем пустым.
    """
    coordinates = row.get("coordinates") or {}
    return {
        "warehouse_id": str(row.get("warehouse_id") or ""),
        "name": row.get("name") or "",
        "address": row.get("address") or "",
        "warehouse_type": row.get("warehouse_type") or "",
        "status": "",
        "is_rfbs": False,
        "latitude": coordinates.get("latitude"),
        "longitude": coordinates.get("longitude"),
        "pause_at": None,
    }


def get_seller_warehouses(client: httpx.Client | None = None):
    """Возвращает склады продавца (FBS/rFBS) с их реальным статусом."""
    own_client = client is None
    if own_client:
        client = _build_client()
    try:
        warehouses = []
        offset = 0
        while True:
            response = _request(
                client,
                WAREHOUSE_LIST_URL,
                {"limit": WAREHOUSE_PAGE_SIZE, "offset": offset},
            )
            batch = response.json().get("warehouses") or []
            warehouses.extend(_parse_seller_warehouse(row) for row in batch)
            if len(batch) < WAREHOUSE_PAGE_SIZE:
                break
            offset += WAREHOUSE_PAGE_SIZE
            time.sleep(SALES_PAGE_PAUSE_SECONDS)
        return warehouses
    finally:
        if own_client:
            client.close()


def search_ozon_warehouses(search: str, client: httpx.Client | None = None):
    """Ищет склады Ozon (FBO) по названию или городу.

    Ozon требует минимум 4 символа. Возвращает тип, адрес и координаты;
    статус приёма поставок через этот метод недоступен.
    """
    search = (search or "").strip()
    if len(search) < OZON_WAREHOUSE_SEARCH_MIN_LENGTH:
        raise ValueError(
            f"Для поиска складов Ozon нужно минимум "
            f"{OZON_WAREHOUSE_SEARCH_MIN_LENGTH} символа"
        )

    own_client = client is None
    if own_client:
        client = _build_client()
    try:
        response = _request(
            client,
            OZON_WAREHOUSE_SEARCH_URL,
            {
                "filter_by_supply_type": OZON_WAREHOUSE_SUPPLY_TYPES,
                "search": search,
            },
        )
        rows = response.json().get("search") or []
        return [_parse_ozon_warehouse(row) for row in rows]
    finally:
        if own_client:
            client.close()
