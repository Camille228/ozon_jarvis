import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from app.database import (
    DB_PATH,
    add_document,
    delete_document,
    init_db,
    log_sync,
    save_products,
    update_costs_bulk,
    update_product_economics,
)
from app.economics import UnitEconomics, to_float
from app.ozon_client import OzonApiError, get_products

# Настраиваем внешний вид страницы
st.set_page_config(page_title="OZON JARVIS", page_icon="📦", layout="wide")

st.title("📦 OZON JARVIS — Система управления")

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ (миграции схемы products/documents) ---
init_db()


def load_products() -> pd.DataFrame:
    """Читает товары из базы. Ошибку БД показываем пользователю, а не глотаем."""
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            return pd.read_sql_query("SELECT * FROM products", conn)
    except sqlite3.Error as e:
        st.error(f"Не удалось прочитать товары из базы: {e}")
        return pd.DataFrame()


def economics_for_row(row) -> UnitEconomics:
    """Собирает юнит-экономику из строки таблицы products."""
    return UnitEconomics.build(
        price=row.get("price"),
        cost_price=row.get("cost_price"),
        commission_rate=row.get("commission_rate"),
        logistics_cost=row.get("logistics_cost"),
        last_mile_cost=row.get("last_mile_cost"),
        packaging_cost=row.get("packaging_cost"),
    )


# --- БЛОК СВЕТОФОРА СВЕЖЕСТИ (честный: по метке времени данных Ozon) ---
data_dt = None
try:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cursor = conn.cursor()

        # Метка времени данных Ozon (когда Ozon реально отдал нам данные)
        cursor.execute("SELECT MAX(ozon_updated_at) FROM products")
        row = cursor.fetchone()
        data_time_raw = row[0] if row else None

        # Фолбэк для старых данных без метки Ozon: время последнего успешного сеанса
        if not data_time_raw:
            cursor.execute(
                "SELECT started_at FROM sync_log WHERE status='success' ORDER BY id DESC LIMIT 1"
            )
            last_sync = cursor.fetchone()
            if last_sync:
                data_time_raw = last_sync[0]

        if data_time_raw:
            try:
                data_dt = datetime.fromisoformat(data_time_raw)
            except ValueError:
                data_dt = None

        if data_dt:
            time_diff = datetime.now() - data_dt

            if time_diff < timedelta(hours=1):
                minutes = int(time_diff.total_seconds() // 60)
                st.success(
                    f"🟢 **Данные актуальны.** Ozon отдал их {minutes} мин. назад "
                    f"({data_dt.strftime('%d.%m.%Y %H:%M')})"
                )
            elif time_diff < timedelta(days=1):
                hours = int(time_diff.total_seconds() // 3600)
                st.warning(
                    f"🟡 **Данные устарели.** Ozon отдал данные {hours} ч. назад "
                    f"({data_dt.strftime('%d.%m.%Y %H:%M')})"
                )
            else:
                st.error(
                    f"🔴 **Данные сильно устарели!** Ozon отдал данные {data_dt.days} дн. назад."
                )
        else:
            st.info("⚪ **Нет данных из Ozon.** Нажмите кнопку ниже, чтобы загрузить.")

        # Честно показываем последнюю неудачную попытку синхронизации
        cursor.execute(
            "SELECT message, started_at FROM sync_log WHERE status='error' ORDER BY id DESC LIMIT 1"
        )
        last_error = cursor.fetchone()
        if last_error:
            try:
                err_dt = datetime.fromisoformat(last_error[1])
            except ValueError:
                err_dt = None
            if err_dt and (data_dt is None or err_dt > data_dt):
                st.error(f"⚠️ **Последняя синхронизация не удалась:** {last_error[0][:300]}")
except sqlite3.Error as e:
    st.error(f"Не удалось прочитать журнал синхронизации: {e}")

st.divider()

# --- БЛОК УПРАВЛЕНИЯ ---
if st.button("🔄 Обновить данные из Ozon", type="primary"):
    with st.spinner("Стучимся в Ozon и обновляем базу..."):
        try:
            products = get_products()
        except OzonApiError as e:
            log_sync("OZON_API", "error", str(e))
            st.error(f"Синхронизация не удалась: {e}")
        else:
            if products:
                save_products(products)
                st.success(f"Успешно обновлено! Получено товаров: {len(products)}")
                st.rerun()
            else:
                log_sync("OZON_API", "warning", "Ozon вернул пустой список товаров")
                st.warning("Товары не найдены (проверьте, есть ли товары в кабинете продавца).")

st.divider()

# --- БЛОК С ТОВАРАМИ ---
st.subheader("Товары в базе")
df_products = load_products()

if df_products.empty:
    st.info("Добавьте товары, чтобы заполнить калькулятор.")
else:
    df_products["Прибыль (Предварительная)"] = df_products.apply(
        lambda row: economics_for_row(row).profit, axis=1
    )
    df_display = df_products.rename(
        columns={
            "sku": "SKU",
            "offer_id": "Артикул",
            "name": "Название",
            "price": "Цена",
            "quantity_fbo": "Остаток FBO",
            "quantity_fbs": "Остаток FBS",
            "cost_price": "Себестоимость",
            "commission_rate": "Комиссия, %",
            "commission_rate_ozon_fbo": "Комиссия Ozon FBO, %",
            "commission_rate_ozon_fbs": "Комиссия Ozon FBS, %",
            "logistics_cost": "Логистика",
            "last_mile_cost": "Последняя миля",
            "packaging_cost": "Упаковка",
            "ozon_updated_at": "Данные Ozon от",
        }
    )[
        [
            "SKU",
            "Артикул",
            "Название",
            "Цена",
            "Остаток FBO",
            "Остаток FBS",
            "Себестоимость",
            "Комиссия, %",
            "Комиссия Ozon FBO, %",
            "Комиссия Ozon FBS, %",
            "Логистика",
            "Последняя миля",
            "Упаковка",
            "Прибыль (Предварительная)",
            "Данные Ozon от",
        ]
    ]
    st.dataframe(df_display, width="stretch")

    # --- МАССОВОЕ ЗАПОЛНЕНИЕ СЕБЕСТОИМОСТИ ---
    st.subheader("Массовое заполнение себестоимости")
    st.caption(
        "Отредактируйте значения в таблице и нажмите «Сохранить». "
        "Пустое поле считается нулевой себестоимостью."
    )
    cost_table = df_products[["sku", "name", "cost_price"]].copy()
    edited_costs = st.data_editor(
        cost_table,
        hide_index=True,
        width="stretch",
        key="bulk_cost_editor",
        column_config={
            "sku": st.column_config.TextColumn("SKU", disabled=True),
            "name": st.column_config.TextColumn("Название", disabled=True, width="large"),
            "cost_price": st.column_config.NumberColumn(
                "Себестоимость, руб.",
                min_value=0.0,
                step=0.01,
                format="%.2f",
            ),
        },
    )
    if st.button("💾 Сохранить себестоимость", key="bulk_cost_save"):
        costs = {
            str(row.sku): to_float(row.cost_price)
            for row in edited_costs.itertuples(index=False)
        }
        try:
            updated = update_costs_bulk(costs)
        except ValueError as e:
            st.error(str(e))
        else:
            st.success(f"Себестоимость сохранена для {updated} товаров.")
            st.rerun()

    # --- КАЛЬКУЛЯТОР ТОВАРА ---
    st.subheader("Калькулятор товара")
    sku_options = df_products["sku"].tolist()
    sku_names = dict(zip(df_products["sku"], df_products["name"]))
    selected_sku = st.selectbox(
        "Товар",
        sku_options,
        format_func=lambda s: f"{s} — {sku_names.get(s, 'Без названия')}",
        key="calculator_sku",
    )
    selected = df_products.loc[df_products["sku"] == selected_sku].iloc[0]

    with st.form("calculator_form"):
        cost = st.number_input(
            "Себестоимость, руб.",
            min_value=0.0,
            value=to_float(selected["cost_price"]),
            step=0.01,
            format="%.2f",
        )
        commission_rate = st.number_input(
            "Комиссия Ozon, % от цены",
            min_value=0.0,
            max_value=100.0,
            value=to_float(selected["commission_rate"]),
            step=0.1,
            format="%.2f",
        )
        logistics_cost = st.number_input(
            "Логистика Ozon, руб. за штуку",
            min_value=0.0,
            value=to_float(selected["logistics_cost"]),
            step=0.01,
            format="%.2f",
        )
        last_mile_cost = st.number_input(
            "Последняя миля / Эквайринг, руб. за штуку",
            min_value=0.0,
            value=to_float(selected["last_mile_cost"]),
            step=0.01,
            format="%.2f",
        )
        packaging_cost = st.number_input(
            "Упаковка, руб.",
            min_value=0.0,
            value=to_float(selected["packaging_cost"]),
            step=0.01,
            format="%.2f",
        )
        submitted = st.form_submit_button("💾 Сохранить")
        if submitted:
            update_product_economics(
                selected_sku,
                cost,
                commission_rate,
                logistics_cost,
                last_mile_cost,
                packaging_cost,
            )
            st.success(f"Данные для SKU {selected_sku} сохранены.")
            st.rerun()

    current_econ = UnitEconomics.build(
        price=selected["price"],
        cost_price=selected["cost_price"],
        commission_rate=selected["commission_rate"],
        logistics_cost=selected["logistics_cost"],
        last_mile_cost=selected["last_mile_cost"],
        packaging_cost=selected["packaging_cost"],
    )
    st.caption(
        f"Цена: {current_econ.price:.2f} руб. | "
        f"Комиссия Ozon: {current_econ.commission:.2f} руб. | "
        f"Предварительная прибыль: {current_econ.profit:.2f} руб."
    )

    st.markdown("**What-If: сценарий прибыли**")
    scenario_price = st.slider(
        "Цена в сценарии, руб.",
        min_value=0.0,
        max_value=max(to_float(selected["price"]) * 2, 1000.0),
        value=to_float(selected["price"]),
        step=10.0,
        key=f"whatif_price_{selected_sku}",
    )
    ad_rate = st.slider(
        "ДРР (реклама), % от выручки",
        min_value=0.0,
        max_value=50.0,
        value=0.0,
        step=0.5,
        key=f"whatif_ad_rate_{selected_sku}",
    )
    scenario_econ = UnitEconomics.build(
        price=scenario_price,
        cost_price=selected["cost_price"],
        commission_rate=selected["commission_rate"],
        logistics_cost=selected["logistics_cost"],
        last_mile_cost=selected["last_mile_cost"],
        packaging_cost=selected["packaging_cost"],
        ad_rate=ad_rate,
    )

    col_profit, col_margin, col_ad = st.columns(3)
    col_profit.metric("Прибыль в сценарии", f"{scenario_econ.profit:,.2f} руб.")
    col_margin.metric("Маржа", f"{scenario_econ.margin:.1f}%")
    col_ad.metric("Расход на рекламу", f"{scenario_econ.ad_cost:,.2f} руб.")

# --- ДОКУМЕНТЫ ---
st.subheader("Документы и сроки действия")
try:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        df_docs = pd.read_sql_query("SELECT * FROM documents ORDER BY expires_at", conn)
except sqlite3.Error as e:
    st.error(f"Не удалось прочитать документы: {e}")
    df_docs = pd.DataFrame()

if df_docs.empty:
    st.info("Документы пока не добавлены.")
else:
    df_docs["days_left"] = (
        pd.to_datetime(df_docs["expires_at"]) - pd.Timestamp.today().normalize()
    ).dt.days
    expired = df_docs[df_docs["days_left"] < 0]
    expiring = df_docs[(df_docs["days_left"] >= 0) & (df_docs["days_left"] <= 30)]
    for _, doc in expired.iterrows():
        st.error(f"🔴 «{doc['title']}» просрочен {abs(int(doc['days_left']))} дн. назад.")
    for _, doc in expiring.iterrows():
        st.warning(f"🟡 «{doc['title']}» истекает через {int(doc['days_left'])} дн.")
    st.dataframe(
        df_docs[["title", "doc_type", "sku", "expires_at", "days_left", "notes"]],
        width="stretch",
    )
    with st.expander("Удалить документ"):
        doc_id = st.selectbox(
            "Документ",
            df_docs["id"].tolist(),
            format_func=lambda i: df_docs.loc[df_docs["id"] == i, "title"].iloc[0],
            key="delete_doc_id",
        )
        if st.button("🗑 Удалить", key="delete_doc_btn"):
            delete_document(doc_id)
            st.rerun()

with st.form("document_form", clear_on_submit=True):
    st.markdown("**Добавить документ**")
    doc_title = st.text_input("Название документа")
    doc_type = st.selectbox("Тип документа", ["Декларация", "Сертификат", "Прочее"])
    doc_sku = st.text_input("SKU (необязательно)")
    expires_at = st.date_input("Действует до")
    doc_notes = st.text_input("Примечание")
    doc_submitted = st.form_submit_button("💾 Добавить документ")
    if doc_submitted:
        if not doc_title.strip():
            st.warning("Укажите название документа.")
        else:
            add_document(
                doc_title.strip(),
                doc_type,
                doc_sku.strip(),
                expires_at.isoformat(),
                doc_notes.strip(),
            )
            st.success("Документ добавлен.")
            st.rerun()

# --- ЖУРНАЛ СИНХРОНИЗАЦИИ ---
st.subheader("Журнал синхронизации (статусы данных)")
try:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        df_log = pd.read_sql_query("SELECT * FROM sync_log ORDER BY id DESC", conn)
except sqlite3.Error as e:
    st.error(f"Не удалось прочитать журнал синхронизации: {e}")
else:
    st.dataframe(df_log, width="stretch")
