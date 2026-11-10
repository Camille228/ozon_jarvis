import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = "data/jarvis.db"

def init_db():
    """Создает базу данных и таблицы, если их еще нет"""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Таблица для товаров (юнит-экономика + данные из Ozon)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        sku TEXT PRIMARY KEY,
        name TEXT,
        price REAL,
        cost_price REAL DEFAULT 0,
        commission_rate REAL DEFAULT 0,
        logistics_cost REAL DEFAULT 0,
        last_mile_cost REAL DEFAULT 0,
        packaging_cost REAL DEFAULT 0,
        offer_id TEXT,
        quantity_fbo INTEGER DEFAULT 0,
        quantity_fbs INTEGER DEFAULT 0,
        commission_rate_ozon_fbo REAL,
        commission_rate_ozon_fbs REAL,
        ozon_updated_at TEXT,
        updated_at TEXT
    )
    """)
    
    # Миграция для уже существующих баз: добавляем недостающие колонки
    cursor.execute("PRAGMA table_info(products)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    # Переименовываем старое поле commission_pct в commission_rate (как в README)
    if "commission_pct" in existing_columns and "commission_rate" not in existing_columns:
        try:
            cursor.execute("ALTER TABLE products RENAME COLUMN commission_pct TO commission_rate")
            existing_columns.remove("commission_pct")
            existing_columns.add("commission_rate")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE products ADD COLUMN commission_rate REAL DEFAULT 0")
            cursor.execute("UPDATE products SET commission_rate = commission_pct")
            existing_columns.add("commission_rate")
    new_columns = {
        "cost_price": "REAL DEFAULT 0",
        "commission_rate": "REAL DEFAULT 0",
        "logistics_cost": "REAL DEFAULT 0",
        "last_mile_cost": "REAL DEFAULT 0",
        "packaging_cost": "REAL DEFAULT 0",
        "offer_id": "TEXT",
        "quantity_fbo": "INTEGER DEFAULT 0",
        "quantity_fbs": "INTEGER DEFAULT 0",
        "commission_rate_ozon_fbo": "REAL",
        "commission_rate_ozon_fbs": "REAL",
        "ozon_updated_at": "TEXT",
    }
    for column, definition in new_columns.items():
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE products ADD COLUMN {column} {definition}")
    
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
    
    # Таблица для документов (декларации, сертификаты)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        doc_type TEXT,
        sku TEXT,
        expires_at TEXT,
        notes TEXT,
        created_at TEXT
    )
    """)
    conn.commit()
    conn.close()

def save_products(items: list):
    """Сохраняет список товаров, полученных от Ozon.

    Экономику, введенную вручную (себестоимость, комиссия, логистика и т.д.),
    при повторной синхронизации не трогаем — обновляем только данные Ozon.
    Новым товарам подставляем комиссию Ozon как стартовое значение.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    ozon_updated_at = items[0].get("ozon_updated_at") if items else now

    for item in items:
        # Стартовая комиссия для нового товара: берем FBS, иначе FBO
        commission_default = (
            item.get("commission_rate_ozon_fbs")
            if item.get("commission_rate_ozon_fbs") is not None
            else item.get("commission_rate_ozon_fbo")
        )
        cursor.execute("""
            INSERT INTO products (
                sku, offer_id, name, price, quantity_fbo, quantity_fbs,
                commission_rate_ozon_fbo, commission_rate_ozon_fbs,
                cost_price, commission_rate, logistics_cost, last_mile_cost, packaging_cost,
                ozon_updated_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 0, 0, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
                offer_id = excluded.offer_id,
                name = excluded.name,
                price = excluded.price,
                quantity_fbo = excluded.quantity_fbo,
                quantity_fbs = excluded.quantity_fbs,
                commission_rate_ozon_fbo = excluded.commission_rate_ozon_fbo,
                commission_rate_ozon_fbs = excluded.commission_rate_ozon_fbs,
                ozon_updated_at = excluded.ozon_updated_at,
                updated_at = excluded.updated_at
        """, (
            item["sku"], item.get("offer_id"), item["name"], item.get("price") or 0,
            int(item.get("quantity_fbo") or 0), int(item.get("quantity_fbs") or 0),
            item.get("commission_rate_ozon_fbo"), item.get("commission_rate_ozon_fbs"),
            commission_default if commission_default is not None else 0,
            item.get("ozon_updated_at") or now, now,
        ))

    # Записываем в журнал
    cursor.execute(
        "INSERT INTO sync_log (source, status, message, started_at) VALUES (?, ?, ?, ?)",
        (
            "OZON_API", "success",
            f"Загружено товаров: {len(items)}. Данные Ozon от {ozon_updated_at}",
            now,
        )
    )

    conn.commit()
    conn.close()
    print(f"Успешно сохранено товаров в базу: {len(items)}")


def log_sync(source: str, status: str, message: str):
    """Записывает в журнал синхронизации ошибку или предупреждение"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sync_log (source, status, message, started_at) VALUES (?, ?, ?, ?)",
        (source, status, message, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

def update_cost(sku: str, cost: float):
    """Обновляет себестоимость товара по его SKU"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET cost_price = ? WHERE sku = ?", (cost, sku))
    conn.commit()
    conn.close()

def update_product_economics(
    sku: str,
    cost_price: float,
    commission_rate: float,
    logistics_cost: float,
    last_mile_cost: float,
    packaging_cost: float,
):
    """Обновляет юнит-экономику товара по его SKU"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE products SET
            cost_price = ?,
            commission_rate = ?,
            logistics_cost = ?,
            last_mile_cost = ?,
            packaging_cost = ?
        WHERE sku = ?
        """,
        (cost_price, commission_rate, logistics_cost, last_mile_cost, packaging_cost, sku),
    )
    conn.commit()
    conn.close()

def add_document(title: str, doc_type: str, sku: str, expires_at: str, notes: str = ""):
    """Добавляет документ со сроком действия"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO documents (title, doc_type, sku, expires_at, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (title, doc_type, sku, expires_at, notes, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

def delete_document(doc_id: int):
    """Удаляет документ по id"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()

def show_products():
    """Показывает товары из базы"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products")
    rows = cursor.fetchall()
    conn.close()

    print("\n--- ТОВАРЫ В БАЗЕ ---")
    for row in rows:
        row = dict(row)
        price = row.get("price") or 0
        cost = row.get("cost_price") or 0
        commission = price * (row.get("commission_rate") or 0) / 100
        other_costs = (
            (row.get("logistics_cost") or 0)
            + (row.get("last_mile_cost") or 0)
            + (row.get("packaging_cost") or 0)
        )
        profit = price - cost - commission - other_costs
        print(
            f"SKU: {row['sku']} | Артикул: {row.get('offer_id') or '-'} | {row.get('name')} | "
            f"Цена: {price} руб. | Остатки FBO/FBS: {row.get('quantity_fbo') or 0}/"
            f"{row.get('quantity_fbs') or 0} | "
            f"Себестоимость: {cost} руб. | Комиссия: {commission} руб. | "
            f"Прибыль: {profit} руб. | Данные Ozon от: {row.get('ozon_updated_at') or '-'}"
        )
    print("---------------------\n")