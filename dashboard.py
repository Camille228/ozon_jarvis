import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from app.database import (
    DB_PATH,
    add_document,
    add_recommendation,
    add_recommendations,
    add_tax_operation,
    delete_document,
    delete_recommendation,
    delete_tax_operation,
    init_db,
    log_sync,
    save_warehouses,
    tax_operations,
    update_costs_bulk,
    update_product_economics,
    update_recommendation,
)
from app.economics import UnitEconomics, to_float
from app.inventory import STALE_STOCK_DAYS
from app.services.ozon_client import (
    OzonApiError,
    get_seller_warehouses,
    search_ozon_warehouses,
)
from app.recommendations import (
    OPEN_RECOMMENDATION_STATUSES,
    RECOMMENDATION_KIND_LABELS,
    RECOMMENDATION_STATUS_LABELS,
    STATUS_BY_LABEL,
    build_stock_recommendations,
)
from app.scheduler import create_background_scheduler, interval_hours_from_env
from app.stock_analysis import build_stock_table
from app.sync import SyncInProgressError, sync_products, sync_sales
from app.tax import (
    OBJECT_INCOME_EXPENSE,
    OBJECT_LABELS,
    TAX_OPERATION_KIND_LABELS,
    ausn_limit_warning,
    compare_ausn,
)
from app.warehouses import (
    seller_status_label,
    warehouse_map_url,
    warehouse_type_label,
)

# Настраиваем внешний вид страницы
st.set_page_config(page_title="OZON JARVIS", page_icon="📦", layout="wide")

st.title("📦 OZON JARVIS — Система управления")

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ (миграции схемы products/documents) ---
init_db()


@st.cache_resource
def start_autosync_scheduler():
    """Запускает фоновый планировщик один раз на процесс Streamlit."""
    scheduler = create_background_scheduler()
    scheduler.start()
    return scheduler


def load_products() -> pd.DataFrame:
    """Читает товары из базы. Ошибку БД показываем пользователю, а не глотаем."""
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            return pd.read_sql_query("SELECT * FROM products", conn)
    except sqlite3.Error as e:
        st.error(f"Не удалось прочитать товары из базы: {e}")
        return pd.DataFrame()


def load_sales() -> pd.DataFrame:
    """Читает ежедневную статистику продаж."""
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            return pd.read_sql_query("SELECT * FROM sales_daily", conn)
    except sqlite3.Error as e:
        st.error(f"Не удалось прочитать статистику продаж: {e}")
        return pd.DataFrame()


def load_stock_snapshots() -> pd.DataFrame:
    """Читает историю срезов остатков."""
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            return pd.read_sql_query(
                "SELECT * FROM stock_snapshots ORDER BY snapshot_date", conn
            )
    except sqlite3.Error as e:
        st.error(f"Не удалось прочитать историю остатков: {e}")
        return pd.DataFrame()


def load_recommendations() -> pd.DataFrame:
    """Читает журнал рекомендаций."""
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            return pd.read_sql_query(
                "SELECT * FROM recommendations ORDER BY id DESC", conn
            )
    except sqlite3.Error as e:
        st.error(f"Не удалось прочитать журнал рекомендаций: {e}")
        return pd.DataFrame()


def load_warehouses() -> pd.DataFrame:
    """Читает справочник складов."""
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            return pd.read_sql_query(
                "SELECT * FROM warehouses ORDER BY source, name", conn
            )
    except sqlite3.Error as e:
        st.error(f"Не удалось прочитать справочник складов: {e}")
        return pd.DataFrame()


def load_tax_operations(date_from: str = "", date_to: str = "") -> pd.DataFrame:
    """Читает операции (доходы/расходы) для расчёта АУСН за период."""
    try:
        rows = tax_operations(date_from, date_to)
    except sqlite3.Error as e:
        st.error(f"Не удалось прочитать операции для расчёта налога: {e}")
        return pd.DataFrame()
    return pd.DataFrame(rows)


def cell_text(value) -> str:
    """Превращает значение из таблицы в строку, не превращая пустое в «nan»."""
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


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
if os.getenv("JARVIS_AUTOSYNC", "0") == "1":
    start_autosync_scheduler()
    st.caption(
        f"🤖 Автосинхронизация включена: каждые {interval_hours_from_env():g} ч."
    )

col_sync_products, col_sync_sales = st.columns(2)

with col_sync_products:
    if st.button("🔄 Обновить товары из Ozon", type="primary"):
        with st.spinner("Стучимся в Ozon и обновляем базу..."):
            try:
                count = sync_products()
            except SyncInProgressError as e:
                log_sync("OZON_API", "warning", str(e))
                st.warning(str(e))
            except OzonApiError as e:
                log_sync("OZON_API", "error", str(e))
                st.error(f"Синхронизация не удалась: {e}")
            else:
                if count:
                    st.success(f"Успешно обновлено! Получено товаров: {count}")
                    st.rerun()
                else:
                    st.warning(
                        "Товары не найдены (проверьте, есть ли товары в кабинете продавца)."
                    )

with col_sync_sales:
    if st.button("📊 Обновить продажи (30 дней)"):
        with st.spinner("Запрашиваем статистику продаж в Ozon..."):
            try:
                count = sync_sales(30)
            except SyncInProgressError as e:
                log_sync("OZON_ANALYTICS", "warning", str(e))
                st.warning(str(e))
            except OzonApiError as e:
                log_sync("OZON_ANALYTICS", "error", str(e))
                st.error(f"Не удалось загрузить продажи: {e}")
            else:
                if count:
                    st.success(f"Продажи обновлены: {count} строк.")
                    st.rerun()
                else:
                    st.warning("Ozon не вернул статистику продаж за период.")

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

# --- БЕЗОПАСНЫЙ ОСТАТОК ---
if not df_products.empty:
    st.subheader("Безопасный остаток")
    df_sales = load_sales()

    if df_sales.empty:
        st.info(
            "Нет данных о продажах. Нажмите «Обновить продажи», чтобы рассчитать, "
            "на сколько дней хватит остатка."
        )
    else:
        period_days = st.selectbox(
            "Период для расчёта, дней",
            [7, 14, 30],
            index=2,
            key="stock_period_days",
        )

        has_dates = not pd.to_datetime(df_sales["day"], errors="coerce").dropna().empty
        analysis = (
            build_stock_table(df_products, df_sales, period_days) if has_dates else None
        )

        if analysis is None:
            if has_dates:
                st.info("За выбранный период продаж нет. Обновите статистику.")
            else:
                st.info("В статистике продаж нет корректных дат. Обновите данные.")
        else:
            stock = analysis.stock
            recent = analysis.recent
            st.caption(
                f"Период: {analysis.period_start.strftime('%d.%m.%Y')} — "
                f"{analysis.period_end.strftime('%d.%m.%Y')}"
            )

            critical_count = int(stock["status"].isin(["out", "critical"]).sum())
            col_sold, col_revenue, col_attention = st.columns(3)
            col_sold.metric("Заказано за период", f"{int(stock['sold'].sum())} шт.")
            col_revenue.metric("Выручка за период", f"{stock['revenue'].sum():,.2f} руб.")
            col_attention.metric("Требуют внимания", f"{critical_count} SKU")

            stock_display = stock.rename(
                columns={
                    "sku": "SKU",
                    "name": "Название",
                    "quantity_fbo": "FBO",
                    "quantity_fbs": "FBS",
                    "stock_total": "Всего",
                    "sold": f"Заказано за {period_days} дн.",
                    "revenue": "Выручка",
                    "avg_daily": "В среднем в день",
                    "days_left_display": "Хватит, дн.",
                    "status_label": "Статус",
                }
            )[
                [
                    "SKU",
                    "Название",
                    "FBO",
                    "FBS",
                    "Всего",
                    f"Заказано за {period_days} дн.",
                    "Выручка",
                    "В среднем в день",
                    "Хватит, дн.",
                    "Статус",
                ]
            ]
            st.dataframe(stock_display, width="stretch", hide_index=True)

            unmatched = analysis.unmatched_skus
            if unmatched:
                st.caption(
                    f"ℹ️ В статистике есть {len(unmatched)} SKU, которых нет в списке "
                    "товаров (возможно, архивные) — они не участвуют в расчёте."
                )

            last_sales_sync = analysis.last_sales_sync
            if last_sales_sync is not None:
                st.caption(
                    f"Статистика продаж обновлена: {last_sales_sync.strftime('%d.%m.%Y %H:%M')}"
                )

            with st.expander("Продажи по дням"):
                daily = recent.sort_values(["day", "sku"], ascending=[False, True])
                st.dataframe(
                    daily[["day", "sku", "ordered_units", "revenue"]].rename(
                        columns={
                            "day": "День",
                            "sku": "SKU",
                            "ordered_units": "Заказано, шт.",
                            "revenue": "Выручка, руб.",
                        }
                    ),
                    width="stretch",
                    hide_index=True,
                )
                st.bar_chart(recent.groupby("day")["ordered_units"].sum())

            # --- ЗАВИСШИЕ И МЕДЛЕННЫЕ ТОВАРЫ ---
            st.subheader("Зависшие и медленные товары")
            stale = stock[stock["is_stale"]].copy()

            if stale.empty:
                st.success("Нет товаров, запаса которых хватает больше чем на 30 дней.")
            else:
                stale["frozen_value"] = stale["stock_total"] * stale["price"]
                stale["stale_reason"] = [
                    "🔴 нет продаж" if a <= 0 else "🟠 > 30 дн."
                    for a in stale["avg_daily"]
                ]
                stale["_sort_stale"] = [
                    float("inf") if pd.isna(d) else d for d in stale["days_left"]
                ]
                stale = stale.sort_values(
                    ["_sort_stale", "stock_total"], ascending=[False, False]
                )

                col_stale, col_frozen, col_frozen_value = st.columns(3)
                col_stale.metric("Зависших/медленных", f"{len(stale)} SKU")
                col_frozen.metric("Заморожено", f"{int(stale['stock_total'].sum())} шт.")
                col_frozen_value.metric(
                    "По цене продажи", f"{stale['frozen_value'].sum():,.2f} руб."
                )

                stale_display = stale.rename(
                    columns={
                        "sku": "SKU",
                        "name": "Название",
                        "stock_total": "Остаток",
                        "sold": f"Заказано за {period_days} дн.",
                        "days_left_display": "Хватит, дн.",
                        "frozen_value": "По цене, руб.",
                        "stale_reason": "Причина",
                    }
                )[
                    [
                        "SKU",
                        "Название",
                        "Остаток",
                        f"Заказано за {period_days} дн.",
                        "Хватит, дн.",
                        "По цене, руб.",
                        "Причина",
                    ]
                ]
                st.dataframe(stale_display, width="stretch", hide_index=True)
                st.caption(
                    "Зависшими считаются товары с остатком, которого хватает больше чем "
                    f"на {int(STALE_STOCK_DAYS)} дней, или товары без продаж за период. "
                    "Оценка по текущей скорости продаж; история остатков накапливается "
                    "с каждой синхронизацией товаров."
                )

# --- ИСТОРИЯ ОСТАТКОВ ---
if not df_products.empty:
    st.subheader("История остатков")
    df_snapshots = load_stock_snapshots()

    if df_snapshots.empty:
        st.info("История появится после синхронизации товаров.")
    else:
        snapshots = df_snapshots.copy()
        snapshots["snapshot_date"] = pd.to_datetime(
            snapshots["snapshot_date"], errors="coerce"
        )
        snapshots["quantity_fbo"] = pd.to_numeric(
            snapshots["quantity_fbo"], errors="coerce"
        ).fillna(0)
        snapshots["quantity_fbs"] = pd.to_numeric(
            snapshots["quantity_fbs"], errors="coerce"
        ).fillna(0)
        snapshots["total"] = snapshots["quantity_fbo"] + snapshots["quantity_fbs"]
        snapshots = snapshots.dropna(subset=["snapshot_date"])

        if snapshots.empty:
            st.info("В истории остатков нет корректных дат.")
        else:
            first_day = snapshots["snapshot_date"].min()
            last_day = snapshots["snapshot_date"].max()
            days_observed = (last_day - first_day).days + 1
            st.caption(
                f"Срезов: {snapshots['snapshot_date'].nunique()} за {days_observed} дн. "
                f"({first_day.strftime('%d.%m.%Y')} — {last_day.strftime('%d.%m.%Y')})."
            )

            history_names = dict(zip(df_products["sku"], df_products["name"]))
            history_sku = st.selectbox(
                "Товар для графика",
                df_products["sku"].tolist(),
                format_func=lambda s: f"{s} — {history_names.get(s, 'Без названия')}",
                key="stock_history_sku",
            )
            sku_history = snapshots[snapshots["sku"] == history_sku].sort_values(
                "snapshot_date"
            )

            if sku_history.empty:
                st.info("По этому товару срезов пока нет.")
            else:
                if sku_history["snapshot_date"].nunique() < 2:
                    st.info(
                        "Пока только один срез — график появится после следующей "
                        "синхронизации товаров."
                    )
                else:
                    st.line_chart(
                        sku_history.set_index("snapshot_date")[["total"]].rename(
                            columns={"total": "Остаток"}
                        )
                    )
                st.dataframe(
                    sku_history[
                        ["snapshot_date", "quantity_fbo", "quantity_fbs", "total"]
                    ].rename(
                        columns={
                            "snapshot_date": "Дата",
                            "quantity_fbo": "FBO",
                            "quantity_fbs": "FBS",
                            "total": "Всего",
                        }
                    ),
                    width="stretch",
                    hide_index=True,
                )

# --- КАРТА СКЛАДОВ OZON ---
st.subheader("Карта складов Ozon")
st.caption(
    "Мои склады со статусом приёма из Ozon и справочник складов Ozon. "
    "Для складов Ozon API не отдаёт статус приёма поставок — показываем "
    "только тип, адрес и координаты."
)

df_warehouses = load_warehouses()
col_my, col_ozon = st.columns(2)

with col_my:
    st.markdown("**🏬 Мои склады (FBS)**")
    my_warehouses = (
        df_warehouses[df_warehouses["source"] == "seller"].copy()
        if not df_warehouses.empty
        else pd.DataFrame()
    )

    if my_warehouses.empty:
        st.info("Склады продавца ещё не загружены — нажмите кнопку ниже.")
    else:
        my_warehouses["status_label"] = my_warehouses["status"].map(
            seller_status_label
        )
        my_warehouses["type_label"] = [
            warehouse_type_label(value, "seller")
            for value in my_warehouses["warehouse_type"]
        ]
        my_warehouses["map_url"] = [
            warehouse_map_url(lat, lon)
            for lat, lon in zip(my_warehouses["latitude"], my_warehouses["longitude"])
        ]
        my_warehouses["synced_at"] = pd.to_datetime(
            my_warehouses["synced_at"], errors="coerce"
        ).dt.strftime("%d.%m.%Y %H:%M")

        st.dataframe(
            my_warehouses[
                ["name", "status_label", "type_label", "address", "map_url", "synced_at"]
            ].rename(
                columns={
                    "name": "Склад",
                    "status_label": "Статус",
                    "type_label": "Тип",
                    "address": "Адрес",
                    "map_url": "Карта",
                    "synced_at": "Обновлено",
                }
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "Карта": st.column_config.LinkColumn(
                    "Карта", display_text="Открыть"
                )
            },
        )

    if st.button("🔄 Обновить мои склады", key="warehouses_refresh_seller"):
        with st.spinner("Запрашиваем склады продавца в Ozon..."):
            try:
                items = get_seller_warehouses()
            except OzonApiError as e:
                log_sync("WAREHOUSE_LIST", "error", str(e))
                st.error(f"Не удалось загрузить склады продавца: {e}")
            else:
                saved = save_warehouses(items, "seller", replace=True)
                if saved:
                    st.success(f"Склады продавца обновлены: {saved}.")
                    st.rerun()
                else:
                    st.warning("Ozon не вернул ни одного склада продавца.")

with col_ozon:
    st.markdown("**🏭 Склады Ozon (FBO)**")
    ozon_warehouses = (
        df_warehouses[df_warehouses["source"] == "ozon"].copy()
        if not df_warehouses.empty
        else pd.DataFrame()
    )

    if ozon_warehouses.empty:
        st.info("Склады Ozon ещё не искали — введите город или название.")
    else:
        ozon_warehouses["type_label"] = [
            warehouse_type_label(value, "ozon")
            for value in ozon_warehouses["warehouse_type"]
        ]
        ozon_warehouses["map_url"] = [
            warehouse_map_url(lat, lon)
            for lat, lon in zip(ozon_warehouses["latitude"], ozon_warehouses["longitude"])
        ]
        st.caption("Показаны все склады, найденные ранее.")
        st.dataframe(
            ozon_warehouses[
                ["name", "type_label", "address", "map_url"]
            ].rename(
                columns={
                    "name": "Склад",
                    "type_label": "Тип",
                    "address": "Адрес",
                    "map_url": "Карта",
                }
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "Карта": st.column_config.LinkColumn(
                    "Карта", display_text="Открыть"
                )
            },
        )

    with st.form("ozon_warehouse_search", clear_on_submit=False):
        search_query = st.text_input(
            "Город или название склада (минимум 4 символа)",
            key="ozon_warehouse_query",
        )
        search_submitted = st.form_submit_button("🔍 Найти склады Ozon")

    if search_submitted:
        query = search_query.strip()
        if len(query) < 4:
            st.warning("Для поиска складов Ozon нужно минимум 4 символа.")
        else:
            with st.spinner("Ищем склады Ozon..."):
                try:
                    items = search_ozon_warehouses(query)
                except OzonApiError as e:
                    log_sync("WAREHOUSE_FBO_SEARCH", "error", str(e))
                    st.error(f"Не удалось найти склады Ozon: {e}")
                else:
                    saved = save_warehouses(items, "ozon")
                    if saved:
                        st.success(f"Найдено и сохранено складов: {saved}.")
                        st.rerun()
                    else:
                        st.warning(
                            "Ozon не вернул склады по этому запросу. "
                            "Попробуйте другой город или название."
                        )

# --- ЖУРНАЛ РЕКОМЕНДАЦИЙ ---
st.subheader("Журнал рекомендаций")
st.caption(
    "Что предложили → что сделали → какой результат. "
    "Записи можно дополнять по мере работы с товаром."
)

df_recommendations = load_recommendations()

if not df_recommendations.empty:
    open_count = int(
        df_recommendations["status"].isin(OPEN_RECOMMENDATION_STATUSES).sum()
    )
    done_count = int((df_recommendations["status"] == "done").sum())
    rejected_count = int((df_recommendations["status"] == "rejected").sum())

    col_total, col_open, col_done, col_rejected = st.columns(4)
    col_total.metric("Всего записей", len(df_recommendations))
    col_open.metric("В работе", open_count)
    col_done.metric("Выполнено", done_count)
    col_rejected.metric("Отклонено", rejected_count)

    journal = df_recommendations.copy()
    journal["status_label"] = journal["status"].map(RECOMMENDATION_STATUS_LABELS)
    journal["kind_label"] = journal["kind"].map(RECOMMENDATION_KIND_LABELS)
    journal["kind_label"] = journal["kind_label"].fillna(journal["kind"])
    journal["created_at"] = pd.to_datetime(
        journal["created_at"], errors="coerce"
    ).dt.strftime("%d.%m.%Y")

    edited_journal = st.data_editor(
        journal[
            [
                "id",
                "sku",
                "kind_label",
                "problem",
                "action",
                "result",
                "status_label",
                "created_at",
            ]
        ],
        hide_index=True,
        width="stretch",
        key="recommendations_editor",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "sku": st.column_config.TextColumn("SKU", disabled=True),
            "kind_label": st.column_config.TextColumn("Тип", disabled=True),
            "problem": st.column_config.TextColumn(
                "Что предложили", disabled=True, width="large"
            ),
            "action": st.column_config.TextColumn("Что сделали", width="large"),
            "result": st.column_config.TextColumn("Результат", width="large"),
            "status_label": st.column_config.SelectboxColumn(
                "Статус",
                options=list(RECOMMENDATION_STATUS_LABELS.values()),
                required=True,
            ),
            "created_at": st.column_config.TextColumn("Создано", disabled=True),
        },
    )

    if st.button("💾 Сохранить журнал", key="recommendations_save"):
        try:
            for row in edited_journal.itertuples(index=False):
                update_recommendation(
                    int(row.id),
                    cell_text(row.action),
                    cell_text(row.result),
                    STATUS_BY_LABEL.get(row.status_label, "new"),
                )
        except ValueError as e:
            st.error(str(e))
        else:
            st.success("Журнал сохранён.")
            st.rerun()

    with st.expander("Удалить запись"):
        rec_id = st.selectbox(
            "Запись",
            df_recommendations["id"].tolist(),
            format_func=lambda i: (
                f"#{i} — "
                f"{str(df_recommendations.loc[df_recommendations['id'] == i, 'problem'].iloc[0])[:70]}"
            ),
            key="delete_recommendation_id",
        )
        if st.button("🗑 Удалить", key="delete_recommendation_btn"):
            delete_recommendation(rec_id)
            st.rerun()
else:
    st.info("Записей пока нет — добавьте первую ниже.")

if not df_products.empty:
    st.markdown("**🤖 Подсказки по текущим остаткам**")
    if df_sales.empty:
        st.caption("Появятся после загрузки статистики продаж.")
    else:
        analysis_for_recs = build_stock_table(df_products, df_sales, 30)
        if analysis_for_recs is None:
            st.caption("Недостаточно данных о продажах за последние 30 дней.")
        else:
            suggestions = build_stock_recommendations(
                analysis_for_recs.stock.to_dict("records")
            )
            if not suggestions:
                st.caption("Актуальных проблем не найдено.")
            else:
                st.caption(
                    f"Найдено подсказок: {len(suggestions)}. Уже открытые "
                    "по тому же SKU и типу не дублируются."
                )
                if st.button(
                    "🤖 Добавить подсказки в журнал", key="recommendations_suggest"
                ):
                    added = add_recommendations(suggestions)
                    if added:
                        st.success(f"Добавлено записей: {added}.")
                        st.rerun()
                    else:
                        st.info("Все актуальные подсказки уже есть в журнале.")

with st.form("recommendation_form", clear_on_submit=True):
    st.markdown("**Добавить рекомендацию вручную**")
    name_by_sku = (
        dict(zip(df_products["sku"].astype(str), df_products["name"]))
        if not df_products.empty
        else {}
    )
    if df_products.empty:
        rec_sku = ""
        st.caption("Товары не загружены — запись будет без привязки к SKU.")
    else:
        rec_sku = st.selectbox(
            "Товар (необязательно)",
            [""] + df_products["sku"].astype(str).tolist(),
            format_func=lambda s: (
                "— без привязки —"
                if not s
                else f"{s} — {name_by_sku.get(s, 'Без названия')}"
            ),
            key="recommendation_sku",
        )

    rec_problem = st.text_area("Что предложили")
    rec_action = st.text_input("Что сделали (можно позже)")
    rec_result = st.text_input("Результат (можно позже)")
    rec_status_label = st.selectbox(
        "Статус",
        list(RECOMMENDATION_STATUS_LABELS.values()),
        key="recommendation_status",
    )
    rec_submitted = st.form_submit_button("💾 Добавить рекомендацию")
    if rec_submitted:
        if not rec_problem.strip():
            st.warning("Опишите, что предложили.")
        else:
            add_recommendation(
                rec_sku,
                rec_problem.strip(),
                action=rec_action.strip(),
                result=rec_result.strip(),
                status=STATUS_BY_LABEL[rec_status_label],
            )
            st.success("Рекомендация добавлена.")
            st.rerun()

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

# --- АУСН: КАЛЬКУЛЯТОР НАЛОГА ---
st.subheader("АУСН: калькулятор налога")
st.caption(
    "Доходы и расходы вводятся вручную — для сверки с налогом, который посчитает "
    "ФНС по банковским выпискам. На АУСН не платятся страховые взносы, поэтому "
    "в расходах они не учитываются. Расчёт здесь предварительный."
)

tax_today = datetime.now().date()
tax_period = st.date_input(
    "Период расчёта",
    (tax_today.replace(month=1, day=1), tax_today),
    key="tax_period",
)
if isinstance(tax_period, (list, tuple)):
    if len(tax_period) == 2:
        tax_from, tax_to = tax_period
    else:
        tax_from = tax_to = tax_period[0]
else:
    tax_from = tax_to = tax_period

df_tax_ops = load_tax_operations(tax_from.isoformat(), tax_to.isoformat())

if df_tax_ops.empty:
    income_total = 0.0
    expense_total = 0.0
    st.info("Операций за период нет — добавьте первую ниже.")
else:
    income_total = df_tax_ops.loc[df_tax_ops["kind"] == "income", "amount"].sum()
    expense_total = df_tax_ops.loc[df_tax_ops["kind"] == "expense", "amount"].sum()

    ops_display = df_tax_ops.copy()
    ops_display["kind_label"] = ops_display["kind"].map(TAX_OPERATION_KIND_LABELS)
    st.dataframe(
        ops_display[["operation_date", "kind_label", "amount", "description"]].rename(
            columns={
                "operation_date": "Дата",
                "kind_label": "Тип",
                "amount": "Сумма, руб.",
                "description": "Описание",
            }
        ),
        width="stretch",
        hide_index=True,
    )

    with st.expander("Удалить операцию"):
        tax_op_id = st.selectbox(
            "Операция",
            df_tax_ops["id"].tolist(),
            format_func=lambda i: (
                f"#{i} — "
                f"{df_tax_ops.loc[df_tax_ops['id'] == i, 'operation_date'].iloc[0]} · "
                f"{TAX_OPERATION_KIND_LABELS.get(df_tax_ops.loc[df_tax_ops['id'] == i, 'kind'].iloc[0], '')} · "
                f"{df_tax_ops.loc[df_tax_ops['id'] == i, 'amount']:,.2f} руб."
            ),
            key="delete_tax_op_id",
        )
        if st.button("🗑 Удалить", key="delete_tax_op_btn"):
            delete_tax_operation(int(tax_op_id))
            st.rerun()

# Подсказка для сверки: заказы в Ozon за выбранный период
df_tax_sales = load_sales()
if not df_tax_sales.empty:
    tax_sales = df_tax_sales.copy()
    tax_sales["day_dt"] = pd.to_datetime(tax_sales["day"], errors="coerce")
    in_period = tax_sales[
        tax_sales["day_dt"].between(pd.Timestamp(tax_from), pd.Timestamp(tax_to))
    ]
    ordered_hint = in_period["revenue"].sum()
    if ordered_hint:
        st.caption(
            f"🧾 Для сверки: заказы в Ozon за период — {ordered_hint:,.2f} руб. "
            "Это заказы, а не поступления денег: на АУСН доход — деньги, "
            "поступившие на счёт."
        )

limit_warning = ausn_limit_warning(income_total)
if limit_warning:
    st.error(f"⚠️ {limit_warning}")

tax_results = compare_ausn(income_total, expense_total)
col_income_tax, col_mixed_tax = st.columns(2)
for tax_result, tax_column in zip(tax_results, (col_income_tax, col_mixed_tax)):
    tax_column.metric(
        OBJECT_LABELS[tax_result.object_type],
        f"{tax_result.tax_to_pay:,.2f} руб.",
    )

if income_total > 0:
    best_tax = min(tax_results, key=lambda r: r.tax_to_pay)
    tax_saving = abs(tax_results[0].tax_to_pay - tax_results[1].tax_to_pay)
    st.caption(
        f"💰 Выгоднее объект «{OBJECT_LABELS[best_tax.object_type]}»: "
        f"заплатите примерно {best_tax.tax_to_pay:,.2f} руб."
        + (f" (экономия {tax_saving:,.2f} руб.)" if tax_saving > 0 else "")
    )

with st.expander("Как считаем (детали)"):
    for tax_result in tax_results:
        st.markdown(f"**{OBJECT_LABELS[tax_result.object_type]}**")
        st.write(
            f"Налоговая база: {tax_result.base:,.2f} руб. × "
            f"{tax_result.rate:.0%} = {tax_result.tax:,.2f} руб."
        )
        if tax_result.object_type == OBJECT_INCOME_EXPENSE:
            st.write(
                f"Минимальный налог (3% от доходов): {tax_result.min_tax:,.2f} руб."
            )
        st.write(
            f"Эффективная ставка: {tax_result.effective_rate:.2f}% · "
            f"К уплате: **{tax_result.tax_to_pay:,.2f} руб.**"
        )

with st.form("tax_operation_form", clear_on_submit=True):
    st.markdown("**Добавить операцию**")
    tax_op_date = st.date_input("Дата операции", tax_today, key="tax_op_date")
    tax_op_kind = st.selectbox(
        "Тип операции",
        list(TAX_OPERATION_KIND_LABELS),
        format_func=lambda k: TAX_OPERATION_KIND_LABELS[k],
        key="tax_op_kind",
    )
    tax_op_amount = st.number_input(
        "Сумма, руб.", min_value=0.01, step=0.01, format="%.2f", key="tax_op_amount"
    )
    tax_op_description = st.text_input(
        "Описание (необязательно)", key="tax_op_description"
    )
    tax_op_submitted = st.form_submit_button("💾 Добавить операцию")
    if tax_op_submitted:
        try:
            add_tax_operation(
                tax_op_date.isoformat(),
                tax_op_kind,
                tax_op_amount,
                tax_op_description,
            )
        except ValueError as e:
            st.error(str(e))
        else:
            st.success("Операция добавлена.")
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
