"""Слой работы с базой данных (Фаза 1 рефакторинга).

Репозитории принимают сессию SQLAlchemy: транзакцией управляет
вызывающий (session_scope или FastAPI-dependency в Фазе 3).
"""

from app.repositories.base import BaseRepository
from app.repositories.documents import DocumentRepository
from app.repositories.products import ProductRepository
from app.repositories.recommendations import RecommendationRepository
from app.repositories.sales import SalesRepository
from app.repositories.stock_snapshots import StockSnapshotRepository
from app.repositories.sync_logs import SyncLogRepository
from app.repositories.tax_operations import TaxOperationRepository
from app.repositories.warehouses import WarehouseRepository

__all__ = [
    "BaseRepository",
    "DocumentRepository",
    "ProductRepository",
    "RecommendationRepository",
    "SalesRepository",
    "StockSnapshotRepository",
    "SyncLogRepository",
    "TaxOperationRepository",
    "WarehouseRepository",
]
