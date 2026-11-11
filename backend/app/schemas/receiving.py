"""Request/response schemas for delivery receipts (Phase 2 Step 3).

Received quantities are validated against the order's outstanding amounts
server-side; the client never posts stock directly. Every receipt records
the name of the employee who physically accepted the goods
(``received_by_staff_name``) in addition to the authenticated account.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class ReceiptLineIn(BaseModel):
    ingredient_id: int
    # Accepted (stocked) quantity; 0 allowed when the line is fully rejected.
    quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    # Damaged/refused quantity: recorded, never stocked, never fulfills.
    quantity_rejected: Decimal = Field(default=Decimal(0), ge=0, max_digits=14, decimal_places=3)


def _validate_staff_name(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("The name of the receiving staff member is required.")
    return stripped


class ReceiveDeliveryIn(BaseModel):
    lines: list[ReceiptLineIn] = Field(min_length=1)
    receipt_date: date | None = None  # defaults to today's business date
    notes: str | None = Field(default=None, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=80)
    received_by_staff_name: str = Field(min_length=1, max_length=120)

    _name = field_validator("received_by_staff_name")(_validate_staff_name)


class ReverseReceiptIn(BaseModel):
    notes: str | None = Field(default=None, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=80)
    received_by_staff_name: str = Field(min_length=1, max_length=120)

    _name = field_validator("received_by_staff_name")(_validate_staff_name)


class ReceiptItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient_id: int
    ingredient_name: str | None = None
    quantity: Decimal
    quantity_rejected: Decimal
    outstanding_quantity: Decimal
    inventory_transaction_id: int | None

    _decimals = field_serializer("quantity", "quantity_rejected", "outstanding_quantity")(lambda v: str(v))


class DeliveryReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    purchase_order_id: int
    received_by: int
    received_by_name: str | None = None  # authenticated account (audit)
    received_by_staff_name: str  # employee who physically received the goods
    received_at: datetime
    receipt_date: date
    notes: str | None
    reversal_of_id: int | None
    is_reversal: bool
    reversed_by_id: int | None
    items: list[ReceiptItemOut] = []
