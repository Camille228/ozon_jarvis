"""Репозиторий журнала синхронизации."""

from datetime import datetime

from sqlalchemy import select

from app.models import SyncLog

from .base import BaseRepository, to_datetime


class SyncLogRepository(BaseRepository):
    """Таблица sync_log: источники, статусы и сообщения синхронизаций."""

    def add(self, source: str, status: str, message: str, started_at=None) -> SyncLog:
        """Записывает в журнал синхронизации. Возвращает созданную запись."""
        log = SyncLog(
            source=source,
            status=status,
            message=message,
            started_at=to_datetime(started_at) or datetime.now(),
        )
        self.session.add(log)
        self.session.flush()
        return log

    def list_recent(self, limit: int = 10) -> list[SyncLog]:
        """Последние записи журнала, новые сверху."""
        stmt = select(SyncLog).order_by(SyncLog.id.desc()).limit(limit)
        return list(self.session.scalars(stmt))
