"""SQLAlchemy ORM models.

Importing this package registers every table on ``Base.metadata``
(used by Alembic). One file per domain area keeps models easy to navigate.
"""

from app.database import Base  # noqa: F401  (re-exported for convenience)
from app.models.audit import AuditLog
from app.models.ingredient import Ingredient, IngredientStockState
from app.models.inventory import InventoryTransaction
from app.models.ordering import PurchaseOrder, PurchaseOrderItem
from app.models.scheduling import SupplierCalendarException, SupplierScheduleRule
from app.models.settings import AppSetting
from app.models.session import UserSession
from app.models.supplier import Supplier, SupplierIngredient
from app.models.unit import Unit
from app.models.user import User

__all__ = [
    "Base",
    "AppSetting",
    "AuditLog",
    "Ingredient",
    "IngredientStockState",
    "InventoryTransaction",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "Supplier",
    "SupplierCalendarException",
    "SupplierIngredient",
    "SupplierScheduleRule",
    "Unit",
    "User",
    "UserSession",
]
