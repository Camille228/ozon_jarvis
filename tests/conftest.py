import pytest

from app import database


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Изолированная тестовая база: реальный data/jarvis.db не трогаем."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    database.init_db()
    return database
