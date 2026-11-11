"""Перенос данных из SQLite (data/jarvis.db) в PostgreSQL — Фаза 1 рефакторинга.

Скрипт идемпотентен, повторный запуск безопасен:
- таблицы с натуральным ключом (products, sales_daily, stock_snapshots,
  warehouses) обновляются актуальными данными из SQLite;
- таблицы с суррогатным id (sync_log, documents, recommendations,
  tax_operations) переносятся с сохранением старых id и не дублируются.

После переноса sequence id-таблиц выставляется за max(id), чтобы новые
записи приложения не сталкивались со старыми id.

Запуск:
    python scripts/migrate_sqlite_to_postgres.py [--sqlite data/jarvis.db]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# Запускаем и без установки пакета: добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, get_engine, session_scope  # noqa: E402
from app.models import (  # noqa: E402
    Document,
    Product,
    Recommendation,
    SalesDaily,
    StockSnapshot,
    SyncLog,
    TaxOperation,
    Warehouse,
)
from app.repositories.base import chunked, to_date, to_datetime  # noqa: E402
from app.repositories.stock_snapshots import StockSnapshotRepository  # noqa: E402

# Таблицы с автоинкрементным id: переносим с сохранением старых id
ID_TABLES = ("sync_log", "documents", "recommendations", "tax_operations")


def upsert(session, model, rows, conflict_cols, update_cols=None) -> int:
    """Массовый upsert в PostgreSQL чанками. update_cols=None → DO NOTHING."""
    inserted = 0
    for chunk in chunked(rows):
        stmt = pg_insert(model).values(chunk)
        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=conflict_cols,
                set_={col: stmt.excluded[col] for col in update_cols},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        session.execute(stmt)
        inserted += len(chunk)
    return inserted


def update_cols_except_pk(model, *pk_cols) -> list[str]:
    """Все колонки модели, кроме ключевых: их обновляем при конфликте."""
    return [c.name for c in model.__table__.columns if c.name not in pk_cols]


def migrate_products(session, rows) -> int:
    data = [
        {
            "sku": row["sku"],
            "name": row.get("name"),
            "price": row.get("price"),
            "cost_price": row.get("cost_price") or 0,
            "commission_rate": row.get("commission_rate") or 0,
            "logistics_cost": row.get("logistics_cost") or 0,
            "last_mile_cost": row.get("last_mile_cost") or 0,
            "packaging_cost": row.get("packaging_cost") or 0,
            "offer_id": row.get("offer_id"),
            "quantity_fbo": row.get("quantity_fbo") or 0,
            "quantity_fbs": row.get("quantity_fbs") or 0,
            "commission_rate_ozon_fbo": row.get("commission_rate_ozon_fbo"),
            "commission_rate_ozon_fbs": row.get("commission_rate_ozon_fbs"),
            "ozon_updated_at": to_datetime(row.get("ozon_updated_at")),
            "updated_at": to_datetime(row.get("updated_at")),
        }
        for row in rows
    ]
    return upsert(session, Product, data, ["sku"], update_cols_except_pk(Product, "sku"))


def migrate_sync_log(session, rows) -> int:
    data = [
        {
            "id": row["id"],
            "source": row.get("source"),
            "status": row.get("status"),
            "message": row.get("message"),
            "started_at": to_datetime(row.get("started_at")),
        }
        for row in rows
    ]
    return upsert(session, SyncLog, data, ["id"])


def migrate_documents(session, rows) -> int:
    data = [
        {
            "id": row["id"],
            "title": row["title"],
            "doc_type": row.get("doc_type"),
            "sku": row.get("sku"),
            "expires_at": to_date(row.get("expires_at")),
            "notes": row.get("notes") or "",
            "created_at": to_datetime(row.get("created_at")),
        }
        for row in rows
    ]
    return upsert(session, Document, data, ["id"])


def migrate_sales_daily(session, rows) -> int:
    data = [
        {
            "sku": row["sku"],
            "day": to_date(row["day"]),
            "ordered_units": row.get("ordered_units") or 0,
            "revenue": row.get("revenue") or 0,
            "synced_at": to_datetime(row.get("synced_at")),
        }
        for row in rows
        if to_date(row["day"]) is not None
    ]
    return upsert(
        session,
        SalesDaily,
        data,
        ["sku", "day"],
        update_cols_except_pk(SalesDaily, "sku", "day"),
    )


def migrate_stock_snapshots(session, rows) -> int:
    data = [
        {
            "sku": row["sku"],
            "snapshot_date": to_date(row["snapshot_date"]),
            "quantity_fbo": row.get("quantity_fbo") or 0,
            "quantity_fbs": row.get("quantity_fbs") or 0,
            "synced_at": to_datetime(row.get("synced_at")),
        }
        for row in rows
        if to_date(row["snapshot_date"]) is not None
    ]
    return upsert(
        session,
        StockSnapshot,
        data,
        ["sku", "snapshot_date"],
        update_cols_except_pk(StockSnapshot, "sku", "snapshot_date"),
    )


def migrate_recommendations(session, rows) -> int:
    data = [
        {
            "id": row["id"],
            "sku": row.get("sku"),
            "kind": row.get("kind") or "manual",
            "problem": row["problem"],
            "action": row.get("action") or "",
            "result": row.get("result") or "",
            "status": row.get("status") or "new",
            "created_at": to_datetime(row.get("created_at")),
            "updated_at": to_datetime(row.get("updated_at")),
        }
        for row in rows
    ]
    return upsert(session, Recommendation, data, ["id"])


def migrate_warehouses(session, rows) -> int:
    data = [
        {
            "warehouse_id": row["warehouse_id"],
            "source": row["source"],
            "name": row.get("name") or "",
            "address": row.get("address") or "",
            "warehouse_type": row.get("warehouse_type") or "",
            "status": row.get("status") or "",
            "is_rfbs": bool(row.get("is_rfbs")),
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "pause_at": to_datetime(row.get("pause_at")),
            "synced_at": to_datetime(row.get("synced_at")),
        }
        for row in rows
    ]
    return upsert(
        session,
        Warehouse,
        data,
        ["warehouse_id", "source"],
        update_cols_except_pk(Warehouse, "warehouse_id", "source"),
    )


def migrate_tax_operations(session, rows) -> int:
    data = [
        {
            "id": row["id"],
            "operation_date": to_date(row["operation_date"]),
            "kind": row["kind"],
            "amount": row.get("amount") or 0.0,
            "description": row.get("description") or "",
            "created_at": to_datetime(row.get("created_at")),
        }
        for row in rows
        if to_date(row["operation_date"]) is not None
    ]
    return upsert(session, TaxOperation, data, ["id"])


# Порядок переноса: сначала справочники, потом зависимости по смыслу
MIGRATIONS = [
    ("products", migrate_products, Product),
    ("sync_log", migrate_sync_log, SyncLog),
    ("documents", migrate_documents, Document),
    ("sales_daily", migrate_sales_daily, SalesDaily),
    ("stock_snapshots", migrate_stock_snapshots, StockSnapshot),
    ("recommendations", migrate_recommendations, Recommendation),
    ("warehouses", migrate_warehouses, Warehouse),
    ("tax_operations", migrate_tax_operations, TaxOperation),
]


def read_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    """Читает таблицу SQLite целиком как список словарей."""
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]  # noqa: S608 — таблица из нашего списка


def sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def pg_count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def reset_sequences(session) -> None:
    """Sequence id-таблиц выставляем за max(id): новые записи не столкнутся со старыми."""
    for table in ID_TABLES:  # noqa: S608 — имена таблиц из нашего списка
        session.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
        ))


def main() -> int:
    parser = argparse.ArgumentParser(description="Перенос SQLite → PostgreSQL (Фаза 1)")
    parser.add_argument(
        "--sqlite",
        default="data/jarvis.db",
        help="путь к старой базе SQLite (по умолчанию data/jarvis.db)",
    )
    args = parser.parse_args()

    if not get_settings().database_url:
        print("DATABASE_URL не задан — заполни .env")
        return 1

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        print(f"Файл SQLite не найден: {sqlite_path}")
        return 1

    engine = get_engine()
    Base.metadata.create_all(engine)
    print("PostgreSQL: таблицы созданы/проверены")

    conn = sqlite3.connect(str(sqlite_path))
    try:
        with session_scope() as session:
            print("\nПеренос данных:")
            for table, migrate, _ in MIGRATIONS:
                rows = read_rows(conn, table)
                saved = migrate(session, rows)
                print(f"  {table:<16} {saved} из {len(rows)} строк")

            # Если история срезов пустая, делаем первый срез из товаров —
            # так же, как это делал init_db в старом app/database.py
            if sqlite_count(conn, "stock_snapshots") == 0 and sqlite_count(conn, "products") > 0:
                created = StockSnapshotRepository(session).backfill_from_products()
                print(f"  stock_snapshots    первый срез из товаров: {created} строк")

            reset_sequences(session)

            print("\nИтог (SQLite → PostgreSQL):")
            for table, _, model in MIGRATIONS:
                print(f"  {table:<16} {sqlite_count(conn, table):>6} → {pg_count(session, model):>6}")
    finally:
        conn.close()

    print("\nГотово: данные перенесены в PostgreSQL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
