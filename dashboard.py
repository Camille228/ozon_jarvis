import streamlit as st
import sqlite3
import pandas as pd

# Настраиваем внешний вид страницы
st.set_page_config(page_title="OZON JARVIS", page_icon="📦", layout="wide")

st.title("📦 OZON JARVIS — Система управления")
st.write("Добро пожаловать! Это тестовый дашборд, который читает данные из нашей базы SQLite.")

# Подключаемся к нашей базе данных
conn = sqlite3.connect("data/jarvis.db")

# --- Блок 1: Товары ---
st.subheader("Товары в базе")
# Читаем данные из таблицы products с помощью Pandas
df_products = pd.read_sql_query("SELECT * FROM products", conn)
# Выводим таблицу на экран
st.dataframe(df_products, width='stretch')

# --- Блок 2: Журнал синхронизации ---
st.subheader("Журнал синхронизации (статусы данных)")
# Читаем данные из таблицы sync_log
df_log = pd.read_sql_query("SELECT * FROM sync_log ORDER BY id DESC", conn)
st.dataframe(df_log, width='stretch')

# Закрываем соединение с базой
conn.close()