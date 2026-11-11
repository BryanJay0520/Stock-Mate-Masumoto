"""Request/response schemas for supplier and ingredient administration.

Quantities are serialized as strings (exact decimals, D4). Ingredient
payloads deliberately contain NO stock-quantity field — balances change only
through the ledger service; the sole exception is ``opening_quantity`` on
creation, which becomes an ``opening_balance`` ledger entry (D23).
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

ContactMethod = Literal["phone", "line", "email", "website", "other"]


def _ser_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class SupplierIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    contact_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    notes: str | None = None
    contact_method: ContactMethod | None = None
    ordering_url: str | None = Field(default=None, max_length=300)
    ordering_procedures: str | None = None


class SupplierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    contact_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    notes: str | None = None
    contact_method: ContactMethod | None = None
    ordering_url: str | None = Field(default=None, max_length=300)
    ordering_procedures: str | None = None


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    contact_name: str | None
    phone: str | None
    email: str | None
    notes: str | None
    contact_method: ContactMethod | None
    ordering_url: str | None
    ordering_procedures: str | None
    status: str
    created_at: datetime


class SupplierIngredientLinkOut(BaseModel):
    ingredient_id: int
    ingredient_name: str
    ingredient_status: str
    is_primary: bool
    package_size: Decimal | None = None
    purchase_package_name: str | None = None
    whole_packages_only: bool = False

    _package_size = field_serializer("package_size")(_ser_decimal)


class PurchasePackagingUpdate(BaseModel):
    """Supplier purchasing package, independent of ingredient counting size."""
    package_size: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=3)
    purchase_package_name: str | None = Field(default=None, max_length=40)
    whole_packages_only: bool = False


class SupplierDetailOut(SupplierOut):
    ingredients: list[SupplierIngredientLinkOut]


class IngredientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    unit_id: int
    min_safety_stock: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=3)
    critical_threshold: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=3)
    usage_per_serving: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    count_package_enabled: bool = False
    count_package_name: str | None = Field(default=None, max_length=40)
    count_units_per_package: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=3)
    primary_supplier_id: int | None = None
    alternative_supplier_ids: list[int] = []
    opening_quantity: Decimal | None = Field(
        default=None, ge=0, max_digits=14, decimal_places=3
    )


class IngredientUpdate(BaseModel):
    """No stock fields here on purpose: balances are ledger-only (D14)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    unit_id: int | None = None
    min_safety_stock: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    critical_threshold: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    usage_per_serving: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=3)
    count_package_enabled: bool | None = None
    count_package_name: str | None = Field(default=None, max_length=40)
    count_units_per_package: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=3)
    primary_supplier_id: int | None = None
    alternative_supplier_ids: list[int] | None = None


class IngredientOut(BaseModel):
    id: int
    name: str
    category: str
    unit_id: int
    unit: str
    min_safety_stock: Decimal
    critical_threshold: Decimal
    usage_per_serving: Decimal
    count_package_enabled: bool
    count_package_name: str | None
    count_units_per_package: Decimal | None
    count_package_version: int
    status: str
    primary_supplier_id: int | None
    supplier_ids: list[int]
    created_at: datetime

    _decimals = field_serializer(
        "min_safety_stock", "critical_threshold", "usage_per_serving",
        "count_units_per_package",
    )(_ser_decimal)


class IngredientDetailOut(IngredientOut):
    current_quantity: Decimal  # read-only projection of the cached balance

    _quantity = field_serializer("current_quantity")(_ser_decimal)
