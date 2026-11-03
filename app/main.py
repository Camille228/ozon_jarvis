from app.database import init_db, save_products, show_products
from app.ozon_client import get_products

if __name__ == "__main__":
    print("Запуск OZON JARVIS...")
    init_db()
    
    print("Стучимся в Ozon...")
    products = get_products()
    
    if products:
        save_products(products)
        show_products()
    else:
        print("Товары не найдены.")