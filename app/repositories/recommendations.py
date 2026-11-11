"""Репозиторий журнала рекомендаций."""

from datetime import datetime

from sqlalchemy import delete, select, update

from app.models import Recommendation
from app.recommendations import OPEN_RECOMMENDATION_STATUSES, RECOMMENDATION_STATUSES

from .base import BaseRepository


def _validate_recommendation_status(status: str) -> str:
    if status not in RECOMMENDATION_STATUSES:
        raise ValueError(f"Неизвестный статус рекомендации: {status!r}")
    return status


class RecommendationRepository(BaseRepository):
    """Таблица recommendations: что предложили → что сделали → какой результат."""

    def add(
        self,
        sku: str,
        problem: str,
        kind: str = "manual",
        action: str = "",
        result: str = "",
        status: str = "new",
    ) -> int:
        """Добавляет запись в журнал рекомендаций. Возвращает id записи."""
        problem = (problem or "").strip()
        if not problem:
            raise ValueError("Рекомендация не может быть пустой")
        _validate_recommendation_status(status)

        now = datetime.now()
        recommendation = Recommendation(
            sku=(sku or "").strip(),
            kind=(kind or "manual").strip(),
            problem=problem,
            action=(action or "").strip(),
            result=(result or "").strip(),
            status=status,
            created_at=now,
            updated_at=now,
        )
        self.session.add(recommendation)
        self.session.flush()
        return recommendation.id

    def add_many(self, items: list[dict]) -> int:
        """Добавляет пачку рекомендаций, пропуская уже открытые по тому же SKU и типу.

        Возвращает число добавленных записей.
        """
        now = datetime.now()
        inserted = 0
        for item in items:
            problem = str(item.get("problem") or "").strip()
            if not problem:
                continue
            sku = str(item.get("sku") or "").strip()
            kind = str(item.get("kind") or "manual").strip()
            exists = self.session.execute(
                select(Recommendation.id).where(
                    Recommendation.sku == sku,
                    Recommendation.kind == kind,
                    Recommendation.status.in_(OPEN_RECOMMENDATION_STATUSES),
                )
            ).first()
            if exists:
                continue
            self.session.add(Recommendation(
                sku=sku,
                kind=kind,
                problem=problem,
                action="",
                result="",
                status="new",
                created_at=now,
                updated_at=now,
            ))
            inserted += 1
        self.session.flush()
        return inserted

    def update(
        self,
        rec_id: int,
        action: str = "",
        result: str = "",
        status: str = "new",
    ) -> int:
        """Обновляет действие, результат и статус записи. Возвращает число строк."""
        _validate_recommendation_status(status)
        statement = self.session.execute(
            update(Recommendation)
            .where(Recommendation.id == rec_id)
            .values(
                action=(action or "").strip(),
                result=(result or "").strip(),
                status=status,
                updated_at=datetime.now(),
            )
        )
        return statement.rowcount

    def delete(self, rec_id: int) -> int:
        """Удаляет запись журнала по id. Возвращает число строк."""
        result = self.session.execute(
            delete(Recommendation).where(Recommendation.id == rec_id)
        )
        return result.rowcount

    def list_all(self, statuses: list[str] | None = None) -> list[Recommendation]:
        """Записи журнала, новые сверху; statuses — фильтр по статусам."""
        stmt = select(Recommendation).order_by(Recommendation.id.desc())
        if statuses:
            stmt = stmt.where(Recommendation.status.in_(statuses))
        return list(self.session.scalars(stmt))
