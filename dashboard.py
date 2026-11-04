import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from app.database import init_db, save_products, update_cost
from app.ozon_client import get_products

# Настраиваем внешний вид страницы
st.set_page_config(page_title="OZON JARVIS", page_icon="📦", layout="wide")

st.title("📦 OZON JARVIS — Система управления")

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ (в т.ч. миграция колонки cost_price) ---
init_db()

# --- БЛОК СВЕТОФОРА СВЕЖЕСТИ ---
conn = sqlite3.connect("data/jarvis.db")

try:
    # Ищем время последней успешной синхронизации
    cursor = conn.cursor()
    cursor.execute("SELECT started_at FROM sync_log WHERE status='success' ORDER BY id DESC LIMIT 1")
    last_sync = cursor.fetchone()
    
    if last_sync:
        last_sync_time = datetime.fromisoformat(last_sync[0])
        now = datetime.now()
        time_diff = now - last_sync_time
        
        # Переводим в удобный формат (минуты/часы)
        if time_diff < timedelta(hours=1):
            minutes = int(time_diff.total_seconds() // 60)
            st.success(f"🟢 **Данные актуальны.** Обновлено {minutes} мин. назад ({last_sync_time.strftime('%H:%M:%S')})")
        elif time_diff < timedelta(days=1):
            hours = int(time_diff.total_seconds() // 3600)
            st.warning(f"🟡 **Данные устарели.** Последнее обновление {hours} ч. назад ({last_sync_time.strftime('%d.%m %H:%M')})")
        else:
            days = time_diff.days
            st.error(f"🔴 **Данные сильно устарели!** Последнее обновление {days} дн. назад.")
    else:
        st.info("⚪ **Нет данных о синхронизации.** Нажмите кнопку ниже, чтобы загрузить данные.")
except Exception as e:
    st.info("⚪ **База данных пока пуста.** Нажмите кнопку ниже.")

st.divider()

# --- БЛОК УПРАВЛЕНИЯ ---
if st.button("🔄 Обновить данные из Ozon", type="primary"):
    with st.spinner("Стучимся в Ozon и обновляем базу..."):
        init_db()
        products = get_products()
        if products:
            save_products(products)
            st.success(f"Успешно обновлено! Получено товаров: {len(products)}")
        else:
            st.warning("Товары не найдены.")
    st.rerun()

st.divider()

# --- БЛОК С ТАБЛИЦАМИ ---
try:
    st.subheader("Товары в базе")
    df_products = pd.read_sql_query("SELECT * FROM products", conn)
    df_products["Прибыль (Предварительная)"] = (
        df_products["price"].fillna(0) - df_products["cost_price"].fillna(0)
    )
    df_products = df_products[
        ["sku", "name", "price", "cost_price", "Прибыль (Предварительная)", "updated_at"]
    ]
    st.dataframe(df_products, width='stretch')

    st.subheader("Себестоимость товаров")
    if not df_products.empty:
        sku_options = df_products["sku"].tolist()
        sku_names = dict(zip(df_products["sku"], df_products["name"]))
        with st.form("cost_form"):
            selected_sku = st.selectbox(
                "SKU товара",
                sku_options,
                format_func=lambda s: f"{s} — {sku_names.get(s, 'Без названия')}",
            )
            cost = st.number_input(
                "Себестоимость, руб.", min_value=0.0, step=0.01, format="%.2f"
            )
            submitted = st.form_submit_button("💾 Сохранить себестоимость")
            if submitted:
                update_cost(selected_sku, cost)
                st.success(
                    f"Себестоимость для SKU {selected_sku} сохранена: {cost:.2f} руб."
                )
                st.rerun()
    else:
        st.info("Добавьте товары, чтобы ввести себестоимость.")

    st.subheader("Журнал синхронизации (статусы данных)")
    df_log = pd.read_sql_query("SELECT * FROM sync_log ORDER BY id DESC", conn)
    st.dataframe(df_log, width='stretch')
except Exception as e:
    st.info("Нет данных для отображения.")
finally:
    conn.close()