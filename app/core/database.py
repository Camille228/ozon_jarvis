"""Подключение к PostgreSQL через SQLAlchemy (Фаза 1 рефакторинга).

Движок и фабрика сессий создаются лениво: импорт модуля не тянет за собой
подключение — это удобно тестам без DATABASE_URL. Модели (app/models)
импортируются отдельно до вызова create_all.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Базовый класс всех ORM-моделей."""


def _with_psycopg_driver(url: str) -> str:
    """DATABASE_URL чаще всего пишут как postgresql://...

    SQLAlchemy ждёт явный драйвер, а мы используем psycopg 3, поэтому
    подставляем его, чтобы строка из .env работала как есть.
    """
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@lru_cache
def get_engine() -> Engine:
    """Движок SQLAlchemy для PostgreSQL (один на процесс)."""
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL не задан — проверь .env")
    # pool_pre_ping: проверяем соединение перед использованием — важно
    # для долгоживущего планировщика (Фаза 2)
    return create_engine(_with_psycopg_driver(url), pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Фабрика сессий. expire_on_commit=False: модели читаются после коммита."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] | None = None,
) -> Iterator[Session]:
    """Сессия с автокоммитом: commit при успехе, rollback при ошибке.

    Через опциональную session_factory вызывающий (тесты, FastAPI-
    зависимости) подставляет свою фабрику — например, привязанную
    к внешней транзакции.
    """
    factory = session_factory or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
