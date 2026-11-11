import pytest
from sqlalchemy.orm import Session, sessionmaker

from app import database
from app.core.config import get_settings
from app.core.database import Base
from app.services import ozon_client

import app.models  # noqa: F401 — регистрируем модели в Base.metadata


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Изолированная тестовая база: реальный data/jarvis.db не трогаем."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    database.init_db()
    return database


@pytest.fixture(autouse=True)
def fast_ozon_requests(monkeypatch):
    """Отключает троттлинг запросов к Ozon — тесты не должны ждать пауз."""
    monkeypatch.setattr(ozon_client, "MIN_REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(ozon_client, "_last_request_monotonic", None)


@pytest.fixture(scope="session")
def pg_engine():
    """Движок PostgreSQL из DATABASE_URL (один на весь прогон тестов)."""
    url = get_settings().database_url
    if not url or not url.startswith("postgres"):
        pytest.skip("Нет DATABASE_URL — тестам репозиториев нужен PostgreSQL")
    from app.core.database import get_engine

    engine = get_engine()
    Base.metadata.create_all(engine)
    yield engine


@pytest.fixture
def pg_connection(pg_engine):
    """Соединение с открытой внешней транзакцией.

    Всё, что тесты пишут через него, откатывается в конце теста —
    реальная база не загрязняется.
    """
    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def db_session(pg_connection):
    """Сессия поверх внешней транзакции: тесты репозиториев не оставляют следов."""
    session = Session(bind=pg_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def session_factory(pg_connection):
    """Фабрика сессий поверх внешней транзакции — для сервисов.

    Сервисы открывают свои сессии через фабрику: commit внутри сервиса
    фиксирует savepoint, но внешняя транзакция откатывается в teardown.
    """
    return sessionmaker(
        bind=pg_connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
