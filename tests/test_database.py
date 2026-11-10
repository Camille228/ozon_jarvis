import sqlite3

import pytest


def sample_product(sku="111", price=1000.0):
    return {
        "sku": sku,
        "offer_id": f"offer-{sku}",
        "name": f"Товар {sku}",
        "price": price,
        "quantity_fbo": 5,
        "quantity_fbs": 2,
        "commission_rate_ozon_fbo": 23.0,
        "commission_rate_ozon_fbs": 24.0,
        "ozon_updated_at": "2026-11-10T10:00:00",
    }


def fetch_one(db, query, params=()):
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query, params).fetchone()
    finally:
        conn.close()


def test_init_db_creates_all_tables(db):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()

    assert {
        "products",
        "sync_log",
        "documents",
        "recommendations",
        "warehouses",
    } <= tables


def test_save_products_inserts_data_and_sync_log(db):
    db.save_products([sample_product()])

    row = fetch_one(db, "SELECT * FROM products WHERE sku='111'")
    assert row["name"] == "Товар 111"
    assert row["price"] == 1000.0
    assert row["quantity_fbo"] == 5
    assert row["quantity_fbs"] == 2
    assert row["commission_rate_ozon_fbo"] == 23.0
    assert row["commission_rate_ozon_fbs"] == 24.0
    # Новичку стартовая комиссия берется из FBS
    assert row["commission_rate"] == 24.0

    log = fetch_one(db, "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1")
    assert log["status"] == "success"
    assert "Загружено товаров: 1" in log["message"]


def test_save_products_creates_daily_stock_snapshot(db):
    db.save_products([sample_product()])

    snapshot = fetch_one(db, "SELECT * FROM stock_snapshots WHERE sku='111'")
    assert snapshot["snapshot_date"] == "2026-11-10"
    assert snapshot["quantity_fbo"] == 5
    assert snapshot["quantity_fbs"] == 2

    updated = sample_product()
    updated["quantity_fbo"] = 7
    db.save_products([updated])

    assert fetch_one(db, "SELECT COUNT(*) FROM stock_snapshots")[0] == 1
    assert (
        fetch_one(db, "SELECT quantity_fbo FROM stock_snapshots WHERE sku='111'")[0]
        == 7
    )


def test_init_db_backfills_stock_snapshot_once(db):
    db.save_products([sample_product()])
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute("DELETE FROM stock_snapshots")
        conn.commit()
    finally:
        conn.close()

    db.init_db()
    db.init_db()

    assert fetch_one(db, "SELECT COUNT(*) FROM stock_snapshots")[0] == 1


def test_save_products_preserves_manual_economics(db):
    db.save_products([sample_product()])
    db.update_product_economics("111", 300.0, 20.0, 50.0, 30.0, 10.0)

    db.save_products([sample_product(price=1200.0)])

    row = fetch_one(db, "SELECT * FROM products WHERE sku='111'")
    assert row["price"] == 1200.0  # данные Ozon обновились
    assert row["cost_price"] == 300.0  # ручная экономика не затёрлась
    assert row["commission_rate"] == 20.0
    assert row["logistics_cost"] == 50.0


def test_update_costs_bulk_updates_every_sku(db):
    db.save_products([sample_product("111"), sample_product("222")])

    updated = db.update_costs_bulk({"111": 100.0, "222": "250.5"})

    assert updated == 2
    assert fetch_one(db, "SELECT cost_price FROM products WHERE sku='111'")[0] == 100.0
    assert fetch_one(db, "SELECT cost_price FROM products WHERE sku='222'")[0] == 250.5


def test_update_costs_bulk_rejects_negative_and_invalid_values(db):
    db.save_products([sample_product()])

    with pytest.raises(ValueError):
        db.update_costs_bulk({"111": -1})
    with pytest.raises(ValueError):
        db.update_costs_bulk({"111": "abc"})

    assert fetch_one(db, "SELECT cost_price FROM products WHERE sku='111'")[0] == 0.0


def test_update_costs_bulk_with_empty_dict_does_nothing(db):
    assert db.update_costs_bulk({}) == 0


def test_log_sync_writes_error(db):
    db.log_sync("OZON_API", "error", "Ключи не заданы")

    log = fetch_one(db, "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1")
    assert log["status"] == "error"
    assert log["message"] == "Ключи не заданы"


def test_save_sales_upserts_rows_and_logs(db):
    rows = [
        {"sku": "111", "day": "2026-11-10", "ordered_units": 3, "revenue": 3000.0},
        {"sku": "222", "day": "2026-11-10", "ordered_units": 1, "revenue": 500.0},
    ]
    assert db.save_sales(rows) == 2

    assert fetch_one(db, "SELECT COUNT(*) FROM sales_daily")[0] == 2
    row = fetch_one(db, "SELECT * FROM sales_daily WHERE sku='111'")
    assert row["ordered_units"] == 3
    assert row["revenue"] == 3000.0

    # Повторная синхронизация обновляет день, а не дублирует его
    db.save_sales(
        [{"sku": "111", "day": "2026-11-10", "ordered_units": 5, "revenue": 4500.0}]
    )
    assert fetch_one(db, "SELECT COUNT(*) FROM sales_daily")[0] == 2
    assert fetch_one(db, "SELECT ordered_units FROM sales_daily WHERE sku='111'")[0] == 5

    log = fetch_one(db, "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1")
    assert log["source"] == "OZON_ANALYTICS"
    assert log["status"] == "success"


def test_save_sales_empty_response_is_a_warning(db):
    assert db.save_sales([]) == 0

    log = fetch_one(db, "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1")
    assert log["status"] == "warning"


def test_documents_add_and_delete(db):
    db.add_document("Сертификат", "Сертификат", "111", "2026-12-01", "важно")
    doc = fetch_one(db, "SELECT * FROM documents")
    assert doc["title"] == "Сертификат"
    assert doc["sku"] == "111"

    db.delete_document(doc["id"])
    assert fetch_one(db, "SELECT * FROM documents") is None


def test_add_recommendation_and_update_status(db):
    rec_id = db.add_recommendation("111", "Поднять цену на 5%")

    row = fetch_one(db, "SELECT * FROM recommendations WHERE id = ?", (rec_id,))
    assert row["sku"] == "111"
    assert row["kind"] == "manual"
    assert row["problem"] == "Поднять цену на 5%"
    assert row["status"] == "new"
    assert row["action"] == ""
    assert row["result"] == ""

    updated = db.update_recommendation(
        rec_id, action="Подняли цену", result="Маржа +3 п.п.", status="done"
    )
    assert updated == 1

    row = fetch_one(db, "SELECT * FROM recommendations WHERE id = ?", (rec_id,))
    assert row["action"] == "Подняли цену"
    assert row["result"] == "Маржа +3 п.п."
    assert row["status"] == "done"
    assert row["updated_at"] >= row["created_at"]


def test_add_recommendation_rejects_empty_problem_and_unknown_status(db):
    with pytest.raises(ValueError):
        db.add_recommendation("111", "   ")
    with pytest.raises(ValueError):
        db.add_recommendation("111", "Нормальная идея", status="unknown")


def test_add_recommendations_skips_open_duplicates(db):
    items = [
        {"sku": "111", "kind": "low_stock", "problem": "Мало остатка"},
        {"sku": "111", "kind": "low_stock", "problem": "Мало остатка (повтор)"},
        {"sku": "222", "kind": "stale_stock", "problem": "Зависший остаток"},
    ]

    assert db.add_recommendations(items) == 2
    assert fetch_one(db, "SELECT COUNT(*) FROM recommendations")[0] == 2

    # Пока запись открыта, повторно она не добавляется
    assert db.add_recommendations(items) == 0

    rec = fetch_one(
        db,
        "SELECT id FROM recommendations WHERE sku='111' AND kind='low_stock'",
    )
    db.update_recommendation(rec["id"], status="done")

    # После закрытия проблемы подсказка может появиться снова
    assert db.add_recommendations(items) == 1


def test_delete_recommendation(db):
    rec_id = db.add_recommendation("111", "Проверить карточку")

    db.delete_recommendation(rec_id)

    assert fetch_one(db, "SELECT * FROM recommendations") is None


def test_save_warehouses_upserts_by_source(db):
    items = [
        {
            "warehouse_id": "111",
            "name": "казань",
            "address": "Россия, Казань",
            "warehouse_type": "fbs",
            "status": "disabled",
            "is_rfbs": False,
            "latitude": 55.8,
            "longitude": 49.1,
            "pause_at": None,
        }
    ]

    assert db.save_warehouses(items, "seller") == 1

    row = fetch_one(db, "SELECT * FROM warehouses")
    assert row["warehouse_id"] == "111"
    assert row["source"] == "seller"
    assert row["name"] == "казань"
    assert row["status"] == "disabled"
    assert row["is_rfbs"] == 0
    assert row["synced_at"]

    # Повторное сохранение обновляет запись, а не создает дубль
    updated = dict(items[0], name="Казань-2", status="active")
    assert db.save_warehouses([updated], "seller") == 1
    assert fetch_one(db, "SELECT COUNT(*) FROM warehouses")[0] == 1

    row = fetch_one(db, "SELECT * FROM warehouses")
    assert row["name"] == "Казань-2"
    assert row["status"] == "active"


def test_save_warehouses_replace_removes_stale_rows_of_same_source(db):
    db.save_warehouses(
        [
            {"warehouse_id": "111", "name": "Старый"},
            {"warehouse_id": "222", "name": "Еще один"},
        ],
        "seller",
    )
    db.save_warehouses(
        [{"warehouse_id": "333", "name": "Новый"}], "seller", replace=True
    )

    conn = sqlite3.connect(db.DB_PATH)
    try:
        ids = [row[0] for row in conn.execute("SELECT warehouse_id FROM warehouses")]
    finally:
        conn.close()

    assert ids == ["333"]


def test_save_warehouses_keeps_other_sources(db):
    db.save_warehouses([{"warehouse_id": "111", "name": "Мой"}], "seller")
    db.save_warehouses([{"warehouse_id": "222", "name": "Ozon"}], "ozon", replace=True)

    assert fetch_one(
        db, "SELECT * FROM warehouses WHERE warehouse_id = '111'"
    ) is not None
    assert fetch_one(
        db, "SELECT * FROM warehouses WHERE warehouse_id = '222'"
    ) is not None


def test_save_warehouses_skips_rows_without_id(db):
    assert db.save_warehouses([{"name": "Без id"}], "seller") == 0
    assert fetch_one(db, "SELECT COUNT(*) FROM warehouses")[0] == 0
