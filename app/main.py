from app.database import init_db, save_products, show_products, log_sync
from app.ozon_client import get_products, OzonApiError

if __name__ == "__main__":
    print("Запуск OZON JARVIS...")
    init_db()

    print("Стучимся в Ozon...")
    try:
        products = get_products()
    except OzonApiError as e:
        print(f"❌ Ошибка синхронизации: {e}")
        log_sync("OZON_API", "error", str(e))
        products = []

    if products:
        save_products(products)
        show_products()
    else:
        print("Товары не найдены (или синхронизация не удалась — см. журнал выше).")
