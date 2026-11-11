"""Централизованная конфигурация приложения (Фаза 1 рефакторинга).

Читает переменные окружения и файл .env. Старый app/config.py не трогаем:
он ещё нужен работающему Streamlit-дашборду, сервисы перейдут сюда в Фазе 2.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения. Источник — переменные окружения и .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Строка подключения к PostgreSQL, например:
    # postgresql://jarvis:password@localhost:5432/ozon_jarvis
    database_url: str = ""

    # Доступы к Ozon Seller API
    ozon_client_id: str | None = None
    ozon_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Кэширует настройки: .env читается один раз за процесс."""
    return Settings()
