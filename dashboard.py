import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from app.database import init_db, save_products
from app.ozon_client import get_products

# Настраиваем внешний вид страницы
st.set_page_config(page_title="OZON JARVIS", page_icon="📦", layout="wide")

st.title("📦 OZON JARVIS — Система управления")

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
    st.dataframe(df_products, width='stretch')

    st.subheader("Журнал синхронизации (статусы данных)")
    df_log = pd.read_sql_query("SELECT * FROM sync_log ORDER BY id DESC", conn)
    st.dataframe(df_log, width='stretch')
except Exception as e:
    st.info("Нет данных для отображения.")
finally:
    conn.close()