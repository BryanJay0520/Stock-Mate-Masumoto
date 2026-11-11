"""Delivery receipts (Phase 2 Step 3, decision D37).

A receipt is the ONLY way ``quantity_received`` on an order item and physical
stock increase together. Receipts are immutable: mistakes are undone by a
reversal receipt (``reversal_of_id``) whose negative item quantities append
compensating ledger rows — never by editing or deleting the original.

Each receipt line links to the ``delivery_receipt`` ledger transaction it
created, so every stock-in row can be traced back to the exact receipt and
purchase order that caused it.
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DeliveryReceipt(Base):
    __tablename__ = "delivery_receipts"
    __table_args__ = (
        Index("ix_delivery_receipts_order", "purchase_order_id"),
        Index("ix_delivery_receipts_receipt_date", "receipt_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=False
    )
    received_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # The account that posted the receipt (received_by) AND the name of the
    # employee who physically accepted the goods are both stored — restaurants
    # often share one account, so the account alone is not enough for audit.
    received_by_staff_name: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=""
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    reversal_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("delivery_receipts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    order: Mapped["PurchaseOrder"] = relationship()  # noqa: F821
    receiver: Mapped["User"] = relationship(foreign_keys=[received_by])  # noqa: F821
    items: Mapped[list["DeliveryReceiptItem"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )


class DeliveryReceiptItem(Base):
    __tablename__ = "delivery_receipt_items"
    __table_args__ = (
        # Positive on a receipt, negative on a reversal — never both zero.
        CheckConstraint(
            "quantity <> 0 OR quantity_rejected <> 0",
            name="ck_receipt_items_nonzero",
        ),
        Index("ix_receipt_items_order_item", "purchase_order_item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(
        ForeignKey("delivery_receipts.id"), nullable=False, index=True
    )
    purchase_order_item_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_order_items.id"), nullable=False
    )
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id"), nullable=False
    )
    # Accepted quantity posted to stock. quantity_rejected records damaged/
    # refused goods: never stocked, never counts as fulfilled (D37).
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    quantity_rejected: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), default=Decimal("0"), nullable=False
    )
    # The ledger row this line created (one-to-one, NULL only if the link
    # predates enforcement — never in practice).
    inventory_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_transactions.id"), unique=True, nullable=True
    )

    receipt: Mapped[DeliveryReceipt] = relationship(back_populates="items")
    ingredient: Mapped["Ingredient"] = relationship()  # noqa: F821
    inventory_transaction: Mapped["InventoryTransaction | None"] = relationship()  # noqa: F821
