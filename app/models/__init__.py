"""ORM-модели PostgreSQL (Фаза 1 рефакторинга).

Схема повторяет таблицы старого SQLite, но типы нативные для
PostgreSQL: TIMESTAMP/DATE вместо ISO-строк, BOOLEAN вместо 0/1.
"""

from app.models.document import Document
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.models.sales_daily import SalesDaily
from app.models.stock_snapshot import StockSnapshot
from app.models.sync_log import SyncLog
from app.models.tax_operation import TaxOperation
from app.models.warehouse import Warehouse

__all__ = [
    "Document",
    "Product",
    "Recommendation",
    "SalesDaily",
    "StockSnapshot",
    "SyncLog",
    "TaxOperation",
    "Warehouse",
]
