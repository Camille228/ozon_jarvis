import pytest

from app import database, ozon_client


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
