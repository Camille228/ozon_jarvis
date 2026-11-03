import sqlite3
import os
from datetime import datetime

DB_PATH = "data/jarvis.db"

def init_db():
    """Создает базу данных и таблицы, если их еще нет"""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Таблица для товаров (добавили price)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        sku TEXT PRIMARY KEY,
        name TEXT,
        price REAL,
        updated_at TEXT
    )
    """)
    
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
    
    # Перебираем товары и сохраняем их
    for item in items:
        cursor.execute(
            "INSERT OR REPLACE INTO products (sku, name, price, updated_at) VALUES (?, ?, ?, ?)",
            (item["sku"], item["name"], item["price"], now)
        )
    
    # Записываем в журнал
    cursor.execute(
        "INSERT INTO sync_log (source, status, message, started_at) VALUES (?, ?, ?, ?)",
        ("OZON_API", "success", f"Загружено товаров: {len(items)}", now)
    )
    
    conn.commit()
    conn.close()
    print(f"Успешно сохранено товаров в базу: {len(items)}")

def show_products():
    """Показывает товары из базы"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products")
    rows = cursor.fetchall()
    conn.close()
    
    print("\n--- ТОВАРЫ В БАЗЕ ---")
    for row in rows:
        print(f"SKU: {row[0]} | Название: {row[1]} | Цена: {row[2]} руб. | Обновлено: {row[3]}")
    print("---------------------\n")