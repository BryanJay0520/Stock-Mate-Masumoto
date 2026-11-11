"""The append-only inventory ledger (decisions D2, D3, D6, D23).

Every change to physical stock is exactly one row here. Rows are never
updated or deleted; corrections are new rows pointing at the mistake via
``supersedes_id``. Replaying ``SUM(quantity_delta)`` per ingredient must
always reproduce ``ingredient_stock_state.current_quantity``.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

TYPE_OPENING_BALANCE = "opening_balance"
TYPE_CONSUMPTION = "consumption"
TYPE_RECONCILIATION = "reconciliation"
TYPE_DELIVERY_RECEIPT = "delivery_receipt"
TYPE_CORRECTION = "correction"
TYPE_ADJUSTMENT = "adjustment"

ALL_TYPES = (
    TYPE_OPENING_BALANCE,
    TYPE_CONSUMPTION,
    TYPE_RECONCILIATION,
    TYPE_DELIVERY_RECEIPT,
    TYPE_CORRECTION,
    TYPE_ADJUSTMENT,
)

MODE_REMAINING = "remaining"
MODE_USED = "used"


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"
    __table_args__ = (
        CheckConstraint(
            "type IN (" + ", ".join(f"'{t}'" for t in ALL_TYPES) + ")",
            name="ck_inventory_transactions_type",
        ),
        CheckConstraint(
            f"mode IS NULL OR mode IN ('{MODE_REMAINING}', '{MODE_USED}')",
            name="ck_inventory_transactions_mode",
        ),
        Index("ix_inventory_transactions_ingredient_id", "ingredient_id", "id"),
        Index("ix_inventory_transactions_business_date", "business_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    resulting_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    entered_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    counting_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_transactions.id"), nullable=True
    )
