"""Репозиторий документов: декларации и сертификаты со сроками."""

from datetime import datetime

from sqlalchemy import delete, select

from app.models import Document

from .base import BaseRepository, to_date


class DocumentRepository(BaseRepository):
    """Таблица documents."""

    def add(
        self, title: str, doc_type: str, sku: str, expires_at, notes: str = ""
    ) -> Document:
        """Добавляет документ со сроком действия. Возвращает запись."""
        document = Document(
            title=title,
            doc_type=doc_type,
            sku=sku,
            expires_at=to_date(expires_at),
            notes=notes,
            created_at=datetime.now(),
        )
        self.session.add(document)
        self.session.flush()
        return document

    def delete(self, doc_id: int) -> int:
        """Удаляет документ по id. Возвращает число строк."""
        result = self.session.execute(
            delete(Document).where(Document.id == doc_id)
        )
        return result.rowcount

    def list_all(self) -> list[Document]:
        """Все документы по дате истечения срока."""
        return list(
            self.session.scalars(select(Document).order_by(Document.expires_at))
        )
