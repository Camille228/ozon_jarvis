import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from app.database import (
    init_db,
    save_products,
    log_sync,
    update_product_economics,
    add_document,
    delete_document,
)
from app.ozon_client import get_products, OzonApiError

def safe_float(value):
    return 0.0 if pd.isna(value) else float(value)

# Настраиваем внешний вид страницы
st.set_page_config(page_title="OZON JARVIS", page_icon="📦", layout="wide")

st.title("📦 OZON JARVIS — Система управления")

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ (миграции схемы products/documents) ---
init_db()

# --- БЛОК СВЕТОФОРА СВЕЖЕСТИ (честный: по метке времени данных Ozon) ---
conn = sqlite3.connect("data/jarvis.db")

try:
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

    data_dt = None
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
except Exception as e:
    st.info("⚪ **База данных пока пуста.** Нажмите кнопку ниже.")

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

# --- БЛОК С ТАБЛИЦАМИ ---
try:
    st.subheader("Товары в базе")
    df_products = pd.read_sql_query("SELECT * FROM products", conn)
    price = df_products["price"].fillna(0)
    cost_price = df_products["cost_price"].fillna(0)
    commission = price * df_products["commission_rate"].fillna(0) / 100
    logistics = df_products["logistics_cost"].fillna(0)
    last_mile = df_products["last_mile_cost"].fillna(0)
    packaging = df_products["packaging_cost"].fillna(0)
    df_products["Прибыль (Предварительная)"] = (
        price - cost_price - commission - logistics - last_mile - packaging
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
    st.dataframe(df_display, width='stretch')

    st.subheader("Калькулятор товара")
    if not df_products.empty:
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
                value=safe_float(selected["cost_price"]),
                step=0.01,
                format="%.2f",
            )
            commission_rate = st.number_input(
                "Комиссия Ozon, % от цены",
                min_value=0.0,
                max_value=100.0,
                value=safe_float(selected["commission_rate"]),
                step=0.1,
                format="%.2f",
            )
            logistics_cost = st.number_input(
                "Логистика Ozon, руб. за штуку",
                min_value=0.0,
                value=safe_float(selected["logistics_cost"]),
                step=0.01,
                format="%.2f",
            )
            last_mile_cost = st.number_input(
                "Последняя миля / Эквайринг, руб. за штуку",
                min_value=0.0,
                value=safe_float(selected["last_mile_cost"]),
                step=0.01,
                format="%.2f",
            )
            packaging_cost = st.number_input(
                "Упаковка, руб.",
                min_value=0.0,
                value=safe_float(selected["packaging_cost"]),
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

        commission_rub = (
            safe_float(selected["price"]) * safe_float(selected["commission_rate"]) / 100
        )
        profit = (
            safe_float(selected["price"])
            - safe_float(selected["cost_price"])
            - commission_rub
            - safe_float(selected["logistics_cost"])
            - safe_float(selected["last_mile_cost"])
            - safe_float(selected["packaging_cost"])
        )
        st.caption(
            f"Цена: {safe_float(selected['price']):.2f} руб. | "
            f"Комиссия Ozon: {commission_rub:.2f} руб. | "
            f"Предварительная прибыль: {profit:.2f} руб."
        )

        st.markdown("**What-If: сценарий прибыли**")
        scenario_price = st.slider(
            "Цена в сценарии, руб.",
            min_value=0.0,
            max_value=max(safe_float(selected["price"]) * 2, 1000.0),
            value=safe_float(selected["price"]),
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
        scenario_commission = (
            scenario_price * safe_float(selected["commission_rate"]) / 100
        )
        ad_cost = scenario_price * ad_rate / 100
        scenario_profit = (
            scenario_price
            - safe_float(selected["cost_price"])
            - scenario_commission
            - safe_float(selected["logistics_cost"])
            - safe_float(selected["last_mile_cost"])
            - safe_float(selected["packaging_cost"])
            - ad_cost
        )
        margin = scenario_profit / scenario_price * 100 if scenario_price else 0.0

        col_profit, col_margin, col_ad = st.columns(3)
        col_profit.metric("Прибыль в сценарии", f"{scenario_profit:,.2f} руб.")
        col_margin.metric("Маржа", f"{margin:.1f}%")
        col_ad.metric("Расход на рекламу", f"{ad_cost:,.2f} руб.")
    else:
        st.info("Добавьте товары, чтобы заполнить калькулятор.")

    st.subheader("Документы и сроки действия")
    df_docs = pd.read_sql_query(
        "SELECT * FROM documents ORDER BY expires_at", conn
    )
    if not df_docs.empty:
        df_docs["days_left"] = (
            pd.to_datetime(df_docs["expires_at"]) - pd.Timestamp.today().normalize()
        ).dt.days
        expired = df_docs[df_docs["days_left"] < 0]
        expiring = df_docs[
            (df_docs["days_left"] >= 0) & (df_docs["days_left"] <= 30)
        ]
        for _, doc in expired.iterrows():
            st.error(
                f"🔴 «{doc['title']}» просрочен {abs(int(doc['days_left']))} дн. назад."
            )
        for _, doc in expiring.iterrows():
            st.warning(f"🟡 «{doc['title']}» истекает через {int(doc['days_left'])} дн.")
        st.dataframe(
            df_docs[["title", "doc_type", "sku", "expires_at", "days_left", "notes"]],
            width='stretch',
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
    else:
        st.info("Документы пока не добавлены.")

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

    st.subheader("Журнал синхронизации (статусы данных)")
    df_log = pd.read_sql_query("SELECT * FROM sync_log ORDER BY id DESC", conn)
    st.dataframe(df_log, width='stretch')
except Exception as e:
    st.info("Нет данных для отображения.")
finally:
    conn.close()