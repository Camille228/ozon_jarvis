import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = "data/jarvis.db"

def init_db():
    """Создает базу данных и таблицы, если их еще нет"""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Таблица для товаров (добавили price и cost_price)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        sku TEXT PRIMARY KEY,
        name TEXT,
        price REAL,
        cost_price REAL DEFAULT 0,
        updated_at TEXT
    )
    """)
    
    # Миграция для уже существующих баз: добавляем cost_price, если ее нет
    cursor.execute("PRAGMA table_info(products)")
    columns = [row[1] for row in cursor.fetchall()]
    if "cost_price" not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN cost_price REAL DEFAULT 0")
    
    # Таблица для журнала синхронизации
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT,
        status TEXT,
        message TEXT,
        started_at TEXT
    )
    """)
    conn.commit()
    conn.close()

def save_products(items: list):
    """Сохраняет список товаров, полученных от Ozon"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    
    # Перебираем товары и сохраняем их.
    # При обновлении существующего товара cost_price не трогаем (сохраняем введенную себестоимость).
    for item in items:
        cursor.execute("""
            INSERT INTO products (sku, name, price, cost_price, updated_at)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(sku) DO UPDATE SET
                name = excluded.name,
                price = excluded.price,
                updated_at = excluded.updated_at
        """, (item["sku"], item["name"], item["price"], now))
    
    # Записываем в журнал
    cursor.execute(
        "INSERT INTO sync_log (source, status, message, started_at) VALUES (?, ?, ?, ?)",
        ("OZON_API", "success", f"Загружено товаров: {len(items)}", now)
    )
    
    conn.commit()
    conn.close()
    print(f"Успешно сохранено товаров в базу: {len(items)}")

def update_cost(sku: str, cost: float):
    """Обновляет себестоимость товара по его SKU"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET cost_price = ? WHERE sku = ?", (cost, sku))
    conn.commit()
    conn.close()

def show_products():
    """Показывает товары из базы"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products")
    rows = cursor.fetchall()
    conn.close()
    
    print("\n--- ТОВАРЫ В БАЗЕ ---")
    for row in rows:
        profit = (row[2] or 0) - (row[3] or 0)
        print(f"SKU: {row[0]} | Название: {row[1]} | Цена: {row[2]} руб. | "
              f"Себестоимость: {row[3]} руб. | Прибыль: {profit} руб. | Обновлено: {row[4]}")
    print("---------------------\n")