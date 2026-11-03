import httpx
from app.config import OZON_CLIENT_ID, OZON_API_KEY

def get_products():
    """Запрашивает список товаров из Ozon (или возвращает фейк для теста)"""
    
    # ВРЕМЕННАЯ ЗАГЛУШКА: Если ключей нет, возвращаем фейковые данные, как будто они пришли от Ozon
    if not OZON_CLIENT_ID or not OZON_API_KEY or OZON_CLIENT_ID == "ваш_числовой_client_id":
        print("⚠️ Ключи Ozon не найдены. Используем имитацию ответа Ozon для теста.")
        return [
            {"sku": "111111", "name": "Смартфон Ozon Тест", "price": 15990.0},
            {"sku": "222222", "name": "Чехол для смартфона", "price": 990.0},
            {"sku": "333333", "name": "Защитное стекло", "price": 450.0}
        ]

    # ---------------------------------------------------------
    # РЕАЛЬНЫЙ КОД (сработает, когда появятся настоящие ключи)
    # ---------------------------------------------------------
    headers = {
        "Client-Id": OZON_CLIENT_ID,
        "Api-Key": OZON_API_KEY,
        "Content-Type": "application/json"
    }
    
    # Шаг 1: Получаем список SKU товаров
    url_list = "https://api-seller.ozon.ru/v3/product/list"
    payload = {"filter": {"visibility": "ALL"}, "last_id": "", "limit": 100}
    
    with httpx.Client() as client:
        response = client.post(url_list, headers=headers, json=payload, timeout=30)
        response.raise_for_status() 
        data = response.json()
        items = data.get("result", {}).get("items", [])
        
    if not items:
        return []

    # Шаг 2: Получаем названия и цены по полученным SKU
    skus = [item["sku"] for item in items]
    url_info = "https://api-seller.ozon.ru/v3/product/info/list"
    
    with httpx.Client() as client:
        response = client.post(url_info, headers=headers, json={"sku": skus}, timeout=30)
        response.raise_for_status()
        info_data = response.json()
        
        # Форматируем данные в удобный для нас вид
        result = []
        for prod in info_data.get("items", []):
            result.append({
                "sku": str(prod.get("sku", "")),
                "name": prod.get("name", "Без названия"),
                # Ozon отдает цену в виде строки, переводим в число
                "price": float(prod.get("price", "0").replace(",", ".")) 
            })
        return result