import sqlite3
import os
from datetime import datetime

from app.economics import UnitEconomics
from app.recommendations import (
    OPEN_RECOMMENDATION_STATUSES,
    RECOMMENDATION_STATUSES,
)

# Путь к базе можно переопределить переменной окружения — это нужно тестам,
# чтобы они не трогали реальные данные.
DB_PATH = os.environ.get("JARVIS_DB_PATH", "data/jarvis.db")

def init_db():
    """Создает базу данных и таблицы, если их еще нет"""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
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

    # Ежедневные продажи по SKU (Ozon Analytics API)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sales_daily (
        sku TEXT NOT NULL,
        day TEXT NOT NULL,
        ordered_units INTEGER DEFAULT 0,
        revenue REAL DEFAULT 0,
        synced_at TEXT,
        PRIMARY KEY (sku, day)
    )
    """)

    # Дневные срезы остатков: история для поиска зависших товаров
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stock_snapshots (
        sku TEXT NOT NULL,
        snapshot_date TEXT NOT NULL,
        quantity_fbo INTEGER DEFAULT 0,
        quantity_fbs INTEGER DEFAULT 0,
        synced_at TEXT,
        PRIMARY KEY (sku, snapshot_date)
    )
    """)

    # Журнал рекомендаций: что предложили, что сделали, какой результат
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS recommendations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT,
        kind TEXT DEFAULT 'manual',
        problem TEXT NOT NULL,
        action TEXT DEFAULT '',
        result TEXT DEFAULT '',
        status TEXT DEFAULT 'new',
        created_at TEXT,
        updated_at TEXT
    )
    """)

    # Первый срез из уже загруженных товаров, чтобы история не начиналась с нуля
    cursor.execute("SELECT COUNT(*) FROM stock_snapshots")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO stock_snapshots (
                sku, snapshot_date, quantity_fbo, quantity_fbs, synced_at
            )
            SELECT
                sku,
                COALESCE(substr(ozon_updated_at, 1, 10), date('now')),
                quantity_fbo,
                quantity_fbs,
                datetime('now')
            FROM products
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

    # Дневной срез остатков: обновляем срез за тот же день, а не дублируем
    snapshot_rows = []
    for item in items:
        snapshot_date = str(item.get("ozon_updated_at") or now)[:10]
        snapshot_rows.append((
            str(item["sku"]),
            snapshot_date,
            int(item.get("quantity_fbo") or 0),
            int(item.get("quantity_fbs") or 0),
            now,
        ))
    if snapshot_rows:
        cursor.executemany(
            """
            INSERT INTO stock_snapshots (
                sku, snapshot_date, quantity_fbo, quantity_fbs, synced_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sku, snapshot_date) DO UPDATE SET
                quantity_fbo = excluded.quantity_fbo,
                quantity_fbs = excluded.quantity_fbs,
                synced_at = excluded.synced_at
            """,
            snapshot_rows,
        )

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


def save_sales(items: list) -> int:
    """Сохраняет ежедневные продажи по SKU, обновляя уже известные дни.

    Возвращает число сохраненных строк. Пустой ответ Ozon честно пишем
    в журнал как предупреждение, а не как успех.
    """
    if not items:
        log_sync("OZON_ANALYTICS", "warning", "Ozon вернул пустую статистику продаж")
        return 0

    now = datetime.now().isoformat()
    rows = [
        (
            str(item["sku"]),
            str(item["day"]),
            int(item.get("ordered_units") or 0),
            float(item.get("revenue") or 0),
            now,
        )
        for item in items
    ]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO sales_daily (sku, day, ordered_units, revenue, synced_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(sku, day) DO UPDATE SET
            ordered_units = excluded.ordered_units,
            revenue = excluded.revenue,
            synced_at = excluded.synced_at
        """,
        rows,
    )
    conn.commit()
    conn.close()

    log_sync("OZON_ANALYTICS", "success", f"Загружено строк продаж: {len(rows)}")
    print(f"Успешно сохранено строк продаж: {len(rows)}")
    return len(rows)


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

def update_costs_bulk(costs: dict) -> int:
    """Массово обновляет себестоимость товаров.

    costs — словарь {sku: себестоимость}. Возвращает число обновленных строк.
    Отрицательная или нечисловая себестоимость не записывается: это ошибка ввода.
    """
    prepared = []
    for sku, cost in costs.items():
        try:
            value = float(cost)
        except (TypeError, ValueError):
            raise ValueError(f"Некорректная себестоимость для SKU {sku}: {cost!r}")
        if value < 0:
            raise ValueError(f"Себестоимость не может быть отрицательной: SKU {sku}")
        prepared.append((value, str(sku)))

    if not prepared:
        return 0

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executemany("UPDATE products SET cost_price = ? WHERE sku = ?", prepared)
    updated = cursor.rowcount
    conn.commit()
    conn.close()
    return updated

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


def _validate_recommendation_status(status: str) -> str:
    if status not in RECOMMENDATION_STATUSES:
        raise ValueError(f"Неизвестный статус рекомендации: {status!r}")
    return status


def add_recommendation(
    sku: str,
    problem: str,
    kind: str = "manual",
    action: str = "",
    result: str = "",
    status: str = "new",
) -> int:
    """Добавляет запись в журнал рекомендаций. Возвращает id записи."""
    problem = (problem or "").strip()
    if not problem:
        raise ValueError("Рекомендация не может быть пустой")
    _validate_recommendation_status(status)

    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO recommendations (
            sku, kind, problem, action, result, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (sku or "").strip(),
            (kind or "manual").strip(),
            problem,
            (action or "").strip(),
            (result or "").strip(),
            status,
            now,
            now,
        ),
    )
    rec_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return rec_id


def add_recommendations(items: list) -> int:
    """Добавляет пачку рекомендаций, пропуская уже открытые по тому же SKU и типу.

    Возвращает число добавленных записей.
    """
    now = datetime.now().isoformat()
    inserted = 0
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for item in items:
        problem = str(item.get("problem") or "").strip()
        if not problem:
            continue
        sku = str(item.get("sku") or "").strip()
        kind = str(item.get("kind") or "manual").strip()
        exists = cursor.execute(
            """
            SELECT 1 FROM recommendations
            WHERE sku = ? AND kind = ? AND status IN (?, ?)
            """,
            (sku, kind, *OPEN_RECOMMENDATION_STATUSES),
        ).fetchone()
        if exists:
            continue
        cursor.execute(
            """
            INSERT INTO recommendations (
                sku, kind, problem, action, result, status, created_at, updated_at
            )
            VALUES (?, ?, ?, '', '', 'new', ?, ?)
            """,
            (sku, kind, problem, now, now),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return inserted


def update_recommendation(
    rec_id: int,
    action: str = "",
    result: str = "",
    status: str = "new",
) -> int:
    """Обновляет действие, результат и статус записи. Возвращает число строк."""
    _validate_recommendation_status(status)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE recommendations SET
            action = ?,
            result = ?,
            status = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            (action or "").strip(),
            (result or "").strip(),
            status,
            datetime.now().isoformat(),
            rec_id,
        ),
    )
    updated = cursor.rowcount
    conn.commit()
    conn.close()
    return updated


def delete_recommendation(rec_id: int):
    """Удаляет запись журнала рекомендаций по id"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM recommendations WHERE id = ?", (rec_id,))
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
        econ = UnitEconomics.build(
            price=row.get("price"),
            cost_price=row.get("cost_price"),
            commission_rate=row.get("commission_rate"),
            logistics_cost=row.get("logistics_cost"),
            last_mile_cost=row.get("last_mile_cost"),
            packaging_cost=row.get("packaging_cost"),
        )
        print(
            f"SKU: {row['sku']} | Артикул: {row.get('offer_id') or '-'} | {row.get('name')} | "
            f"Цена: {econ.price} руб. | Остатки FBO/FBS: {row.get('quantity_fbo') or 0}/"
            f"{row.get('quantity_fbs') or 0} | "
            f"Себестоимость: {econ.cost_price} руб. | Комиссия: {econ.commission} руб. | "
            f"Прибыль: {econ.profit} руб. | Данные Ozon от: {row.get('ozon_updated_at') or '-'}"
        )
    print("---------------------\n")