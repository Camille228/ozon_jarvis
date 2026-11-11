"""Базовый репозиторий и общие помощники слоя работы с базой."""

from collections.abc import Iterator
from datetime import date, datetime

from sqlalchemy import Insert
from sqlalchemy.orm import Session

# Число строк в одном upsert-запросе: не упираемся в лимит параметров
# одного SQL-запроса ни в PostgreSQL, ни в SQLite.
UPSERT_CHUNK_SIZE = 200


def chunked(rows: list, size: int = UPSERT_CHUNK_SIZE) -> Iterator[list]:
    """Нарезает список на куски по size элементов."""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def to_datetime(value) -> datetime | None:
    """Приводит ISO-строки из старого SQLite к datetime; мусор — к None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def to_date(value) -> date | None:
    """Приводит 'ГГГГ-ММ-ДД' (и ISO-строки с временем) к date; мусор — к None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class BaseRepository:
    """Репозиторий привязан к сессии; транзакцией управляет вызывающий."""

    def __init__(self, session: Session):
        self.session = session

    def _insert(self, model) -> Insert:
        """INSERT-конструкция под диалект текущей СУБД.

        PostgreSQL — в бою, SQLite — в тестах: оба диалекта умеют
        ON CONFLICT DO UPDATE / DO NOTHING.
        """
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        return insert(model)

    def _upsert_many(
        self,
        model,
        rows: list[dict],
        conflict_cols: list[str],
        update_cols: list[str] | None = None,
    ) -> int:
        """Массовый upsert чанками. Возвращает число обработанных строк.

        update_cols — колонки, обновляемые при конфликте; None означает
        ON CONFLICT DO NOTHING (не дублируем уже перенесённые записи).
        """
        total = 0
        for chunk in chunked(rows):
            stmt = self._insert(model).values(chunk)
            if update_cols:
                stmt = stmt.on_conflict_do_update(
                    index_elements=conflict_cols,
                    set_={col: stmt.excluded[col] for col in update_cols},
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
            self.session.execute(stmt)
            total += len(chunk)
        return total
