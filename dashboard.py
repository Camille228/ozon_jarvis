import streamlit as st
import sqlite3
import pandas as pd
from app.database import init_db, save_products
from app.ozon_client import get_products

# Настраиваем внешний вид страницы
st.set_page_config(page_title="OZON JARVIS", page_icon="📦", layout="wide")

st.title("📦 OZON JARVIS — Система управления")
st.write("Добро пожаловать! Это тестовый дашборд, который читает данные из нашей базы SQLite.")

# --- БЛОК УПРАВЛЕНИЯ ---
st.subheader("Управление")
# Создаем кнопку. Если её нажали, выполняется код внутри блока if
if st.button("🔄 Обновить данные из Ozon", type="primary"):
    with st.spinner("Стучимся в Ozon и обновляем базу..."):
        init_db() # Создаем базу, если её нет
        products = get_products() # Получаем данные
        if products:
            save_products(products) # Сохраняем
            st.success(f"Успешно обновлено! Получено товаров: {len(products)}")
        else:
            st.warning("Товары не найдены.")
    # Перезапускаем страницу, чтобы таблицы ниже обновились новыми данными
    st.rerun()

st.divider() # Просто рисует красивую линию-разделитель

# --- БЛОК С ТАБЛИЦАМИ ---
# Подключаемся к нашей базе данных
conn = sqlite3.connect("data/jarvis.db")

try:
    # Блок 1: Товары
    st.subheader("Товары в базе")
    df_products = pd.read_sql_query("SELECT * FROM products", conn)
    st.dataframe(df_products, width='stretch')

    # Блок 2: Журнал синхронизации
    st.subheader("Журнал синхронизации (статусы данных)")
    df_log = pd.read_sql_query("SELECT * FROM sync_log ORDER BY id DESC", conn)
    st.dataframe(df_log, width='stretch')
except Exception as e:
    st.info("База данных пока пуста. Нажмите кнопку «Обновить данные из Ozon» выше.")
finally:
    conn.close()