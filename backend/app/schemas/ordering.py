"""Request/response schemas for purchase orders (Phase 2 Step 2).

Quantities are serialized as strings (exact decimals, D4). Pending incoming
quantities are always computed server-side from the order items — never
trusted from the client.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

OrderStatus = Literal[
    "draft", "ordered", "confirmed", "partially_delivered",
    "delivered", "delayed", "cancelled",
]


def _ser_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class OrderItemIn(BaseModel):
    ingredient_id: int
    quantity_ordered: Decimal = Field(gt=0, max_digits=14, decimal_places=3)


class PurchaseOrderIn(BaseModel):
    supplier_id: int
    expected_delivery_date: date
    notes: str | None = None
    items: list[OrderItemIn] = Field(min_length=1)


class PurchaseOrderUpdate(BaseModel):
    """Draft orders only: expected date, notes, and the full item list."""

    expected_delivery_date: date | None = None
    notes: str | None = None
    items: list[OrderItemIn] | None = Field(default=None, min_length=1)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient_id: int
    ingredient_name: str | None = None
    quantity_ordered: Decimal
    quantity_received: Decimal
    pending_quantity: Decimal | None = None

    _decimals = field_serializer(
        "quantity_ordered", "quantity_received", "pending_quantity"
    )(_ser_decimal)


class PurchaseOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_id: int
    supplier_name: str | None = None
    status: str
    expected_delivery_date: date
    created_by: int
    created_by_name: str | None = None
    ordered_by: int | None
    ordered_by_name: str | None = None
    ordered_at: datetime | None
    notes: str | None
    created_at: datetime
    items: list[OrderItemOut] = []


class IncomingSummary(BaseModel):
    """Server-computed pending supply for one ingredient (D36)."""

    incoming_quantity: Decimal
    incoming_ordered_quantity: Decimal   # uncertain: ordered / delayed
    incoming_confirmed_quantity: Decimal  # reliable: confirmed / partially delivered
    expected_delivery: date | None       # earliest pending delivery date
    incoming_status: str | None          # ordered | confirmed | delayed | None
    projected_quantity: Decimal          # available + incoming (clearly a projection)

    _decimals = field_serializer(
        "incoming_quantity", "incoming_ordered_quantity",
        "incoming_confirmed_quantity", "projected_quantity",
    )(_ser_decimal)
