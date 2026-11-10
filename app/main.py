from app.database import init_db, save_products, save_sales, show_products, log_sync
from app.ozon_client import get_products, get_sales, OzonApiError

if __name__ == "__main__":
    print("Запуск OZON JARVIS...")
    init_db()

    print("Стучимся в Ozon за товарами...")
    try:
        products = get_products()
    except OzonApiError as e:
        print(f"❌ Ошибка синхронизации товаров: {e}")
        log_sync("OZON_API", "error", str(e))
        products = []
    else:
        if products:
            save_products(products)
            show_products()
        else:
            print("Товары не найдены (или синхронизация не удалась — см. журнал выше).")

        print("Стучимся в Ozon за продажами...")
        try:
            sales = get_sales(30)
        except OzonApiError as e:
            print(f"❌ Ошибка синхронизации продаж: {e}")
            log_sync("OZON_ANALYTICS", "error", str(e))
        else:
            save_sales(sales)
