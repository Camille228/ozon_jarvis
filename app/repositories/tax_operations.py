"""Репозиторий операций для калькулятора АУСН."""

from datetime import date, datetime

from sqlalchemy import delete, select

from app.models import TaxOperation
from app.tax import TAX_OPERATION_KINDS

from .base import BaseRepository, to_date


def _validate_tax_operation_kind(kind: str) -> str:
    if kind not in TAX_OPERATION_KINDS:
        raise ValueError(f"Неизвестный тип операции: {kind!r}")
    return kind


class TaxOperationRepository(BaseRepository):
    """Таблица tax_operations: ручной ввод доходов и расходов."""

    def add(self, operation_date, kind: str, amount, description: str = "") -> int:
        """Добавляет доход или расход для расчёта АУСН. Возвращает id операции.

        Дата — в формате ГГГГ-ММ-ДД (или date). Сумма — положительное число.
        """
        _validate_tax_operation_kind(kind)

        try:
            value = float(amount)
        except (TypeError, ValueError):
            raise ValueError(f"Некорректная сумма операции: {amount!r}")
        if value != value or value <= 0:  # NaN или неположительное
            raise ValueError(f"Сумма операции должна быть положительной: {amount!r}")

        normalized_date = self._normalize_operation_date(operation_date)

        operation = TaxOperation(
            operation_date=normalized_date,
            kind=kind,
            amount=value,
            description=(description or "").strip(),
            created_at=datetime.now(),
        )
        self.session.add(operation)
        self.session.flush()
        return operation.id

    @staticmethod
    def _normalize_operation_date(operation_date) -> date:
        raw_date = str(operation_date or "").strip()
        if not raw_date:
            raise ValueError("Укажите дату операции")
        normalized = to_date(raw_date)
        if normalized is None:
            raise ValueError(
                f"Дата операции должна быть в формате ГГГГ-ММ-ДД: {raw_date!r}"
            )
        return normalized

    def delete(self, op_id: int) -> int:
        """Удаляет операцию АУСН по id. Возвращает число строк."""
        result = self.session.execute(
            delete(TaxOperation).where(TaxOperation.id == op_id)
        )
        return result.rowcount

    def list_by_period(
        self, date_from: str = "", date_to: str = ""
    ) -> list[TaxOperation]:
        """Операции за период, новые сверху.

        date_from / date_to — границы периода в формате ГГГГ-ММ-ДД, обе
        включаются. Пустая строка — без ограничения с этой стороны.
        """
        stmt = select(TaxOperation)
        date_from_value = to_date(str(date_from or "").strip())
        date_to_value = to_date(str(date_to or "").strip())
        if date_from_value is not None:
            stmt = stmt.where(TaxOperation.operation_date >= date_from_value)
        if date_to_value is not None:
            stmt = stmt.where(TaxOperation.operation_date <= date_to_value)
        stmt = stmt.order_by(
            TaxOperation.operation_date.desc(), TaxOperation.id.desc()
        )
        return list(self.session.scalars(stmt))
