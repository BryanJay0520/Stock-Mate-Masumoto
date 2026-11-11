"""Purchase orders and their line items (Phase 2 Step 2).

Semantics (architecture §4.2 / §5.4, decision D36):
- ``draft`` orders are NEVER supply: they do not count as incoming stock.
- ``ordered`` / ``delayed`` count as ordered-but-unconfirmed incoming
  (uncertain). ``confirmed`` / ``partially_delivered`` count as confirmed
  incoming (reliable). ``cancelled`` counts as nothing.
- Pending quantity per item is ``quantity_ordered - quantity_received``;
  receipts (Phase 2 Step 3) only ever increase ``quantity_received`` and the
  ledger — never the other way around.
- Orders keep their own ``supplier_id`` forever, even if the supplier is
  later archived (historical integrity).
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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin

ORDER_DRAFT = "draft"
ORDER_ORDERED = "ordered"
ORDER_CONFIRMED = "confirmed"
ORDER_PARTIALLY_DELIVERED = "partially_delivered"
ORDER_DELIVERED = "delivered"
ORDER_DELAYED = "delayed"
ORDER_CANCELLED = "cancelled"
ORDER_STATUSES = (
    ORDER_DRAFT,
    ORDER_ORDERED,
    ORDER_CONFIRMED,
    ORDER_PARTIALLY_DELIVERED,
    ORDER_DELIVERED,
    ORDER_DELAYED,
    ORDER_CANCELLED,
)

# Statuses that make quantities count as incoming stock (D36).
INCOMING_UNCONFIRMED_STATUSES = (ORDER_ORDERED, ORDER_DELAYED)
INCOMING_CONFIRMED_STATUSES = (ORDER_CONFIRMED, ORDER_PARTIALLY_DELIVERED)
# Statuses that block supplier archival (open work).
OPEN_ORDER_STATUSES = (
    ORDER_DRAFT,
    ORDER_ORDERED,
    ORDER_CONFIRMED,
    ORDER_PARTIALLY_DELIVERED,
    ORDER_DELAYED,
)


class PurchaseOrder(TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in ORDER_STATUSES) + ")",
            name="ck_orders_status",
        ),
        Index("ix_orders_supplier_status", "supplier_id", "status"),
        Index("ix_orders_expected_date", "expected_delivery_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default=ORDER_DRAFT, nullable=False)
    expected_delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    ordered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    ordered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    supplier: Mapped["Supplier"] = relationship()  # noqa: F821
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])  # noqa: F821
    ordered_by_user: Mapped["User | None"] = relationship(foreign_keys=[ordered_by])  # noqa: F821
    items: Mapped[list["PurchaseOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (
        # One line per ingredient per order: pending quantities can never be
        # double-counted through duplicated lines.
        UniqueConstraint("order_id", "ingredient_id", name="uq_order_items_ingredient"),
        CheckConstraint("quantity_ordered > 0", name="ck_order_items_ordered_positive"),
        CheckConstraint("quantity_received >= 0", name="ck_order_items_received_nonneg"),
        CheckConstraint(
            "quantity_received <= quantity_ordered",
            name="ck_order_items_received_within_ordered",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=False, index=True
    )
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id"), nullable=False
    )
    quantity_ordered: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False
    )
    quantity_received: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), default=Decimal("0"), nullable=False
    )

    order: Mapped[PurchaseOrder] = relationship(back_populates="items")
    ingredient: Mapped["Ingredient"] = relationship()  # noqa: F821
