"""Smoke-проверка Фазы 1: товары и логи читаются и пишутся в PostgreSQL.

Создаёт недостающие таблицы, печатает количество записей, читает товары
и журнал синхронизации через репозитории и добавляет контрольную запись
в журнал. Запуск: python scripts/check_db.py
"""

import sys
from pathlib import Path

# Запускаем и без установки пакета: добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, get_engine, session_scope  # noqa: E402
from app.economics import UnitEconomics  # noqa: E402
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
from app.repositories import ProductRepository, SyncLogRepository  # noqa: E402


def main() -> int:
    if not get_settings().database_url:
        print("DATABASE_URL не задан — заполни .env")
        return 1

    engine = get_engine()
    Base.metadata.create_all(engine)

    models = [
        Product,
        SyncLog,
        Document,
        SalesDaily,
        StockSnapshot,
        Recommendation,
        Warehouse,
        TaxOperation,
    ]

    with session_scope() as session:
        print("Записи в PostgreSQL:")
        for model in models:
            count = session.scalar(select(func.count()).select_from(model)) or 0
            print(f"  {model.__tablename__:<17} {count}")

        products = ProductRepository(session).list_all()
        print(f"\nТовары читаются через репозиторий: {len(products)} шт.")
        for product in products[:5]:
            econ = UnitEconomics.build(
                price=product.price,
                cost_price=product.cost_price,
                commission_rate=product.commission_rate,
                logistics_cost=product.logistics_cost,
                last_mile_cost=product.last_mile_cost,
                packaging_cost=product.packaging_cost,
            )
            print(
                f"  {product.sku}: {product.name} | цена {econ.price} руб. | "
                f"прибыль {econ.profit} руб. | Ozon от: "
                f"{product.ozon_updated_at or '-'}"
            )

        logs = SyncLogRepository(session).list_recent(limit=5)
        print(f"\nЖурнал синхронизации (последние {len(logs)}):")
        for log in logs:
            print(f"  [{log.started_at}] {log.source} {log.status}: {log.message}")

        SyncLogRepository(session).add(
            "CHECK_DB",
            "success",
            "Проверка Фазы 1: запись и чтение PostgreSQL работают",
        )

    print("\nOK: данные читаются и пишутся в PostgreSQL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
