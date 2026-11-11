"""Request/response schemas for the inventory ledger API.

Quantities cross the API as exact values: they are accepted as Decimals and
serialized back as STRINGS so no floating-point error ever appears in
clients (decision D4).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_serializer


def _ser_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class StockEntryRequest(BaseModel):
    ingredient_id: int
    mode: Literal["remaining", "used"]
    quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    reason: str | None = Field(default=None, max_length=40)
    idempotency_key: str = Field(min_length=8, max_length=80)
    confirmed: bool = False
    allow_negative: bool = False  # honored for administrators only


class CorrectionRequest(BaseModel):
    corrected_quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    reason: str = Field(min_length=1, max_length=40)
    idempotency_key: str = Field(min_length=8, max_length=80)
    confirmed: bool = False


class TransactionOut(BaseModel):
    id: int
    ingredient_id: int
    type: str
    quantity_delta: Decimal
    resulting_quantity: Decimal
    previous_quantity: Decimal  # derived: resulting - delta
    reason: str | None
    mode: str | None
    entered_by: int
    business_date: date
    created_at: datetime
    idempotency_key: str | None
    supersedes_id: int | None

    _decimals = field_serializer("quantity_delta", "resulting_quantity", "previous_quantity")(
        _ser_decimal
    )


class StockEntryResponse(BaseModel):
    transaction: TransactionOut
    idempotent_replay: bool  # True when a retry returned the original row


class StockOut(BaseModel):
    ingredient_id: int
    ingredient_name: str
    category: str
    unit: str
    status: str
    current_quantity: Decimal
    last_counted_at: datetime | None
    last_counted_by: int | None
    last_transaction_at: datetime | None
    confidence: str
    # Pending supply from purchase orders (D36) — server-computed, never
    # part of the physical balance. ``projected_quantity`` is an estimate
    # (available + incoming), always distinct from ``current_quantity``.
    incoming_quantity: Decimal = Decimal("0")
    incoming_ordered_quantity: Decimal = Decimal("0")
    incoming_confirmed_quantity: Decimal = Decimal("0")
    expected_delivery: date | None = None
    incoming_status: str | None = None
    projected_quantity: Decimal | None = None

    _decimals = field_serializer(
        "current_quantity", "incoming_quantity", "incoming_ordered_quantity",
        "incoming_confirmed_quantity", "projected_quantity",
    )(_ser_decimal)


class LedgerCheckRow(BaseModel):
    ingredient_id: int
    cached_balance: Decimal
    ledger_balance: Decimal
    consistent: bool

    _decimals = field_serializer("cached_balance", "ledger_balance")(_ser_decimal)


class LedgerVerifyResponse(BaseModel):
    consistent: bool
    rows: list[LedgerCheckRow]


class LedgerRepairResponse(BaseModel):
    checked: int
    drifted: int
    repaired_ingredient_ids: list[int]
    repaired_at: datetime
