from app.database import init_db, show_products, log_sync
from app.ozon_client import OzonApiError
from app.sync import sync_products, sync_sales

if __name__ == "__main__":
    print("Запуск OZON JARVIS...")
    init_db()

    print("Стучимся в Ozon за товарами...")
    try:
        products_count = sync_products()
    except OzonApiError as e:
        print(f"❌ Ошибка синхронизации товаров: {e}")
        log_sync("OZON_API", "error", str(e))
    else:
        if products_count:
            show_products()
        else:
            print("Товары не найдены (или синхронизация не удалась — см. журнал выше).")

        print("Стучимся в Ozon за продажами...")
        try:
            sync_sales(30)
        except OzonApiError as e:
            print(f"❌ Ошибка синхронизации продаж: {e}")
            log_sync("OZON_ANALYTICS", "error", str(e))
