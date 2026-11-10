"""Ingredients (catalog) and their stock state (one canonical balance, D14).

``Ingredient`` describes WHAT an item is — name, category, thresholds,
supplier assignment. It deliberately has NO stock quantity column.

``IngredientStockState`` (strictly 1:1) holds the ONLY cached stock balance
plus the data used by the Stock Confidence Indicator. The balance is only
ever changed together with a ledger row, inside one transaction (D2/D15).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


class Ingredient(TimestampMixin, Base):
    __tablename__ = "ingredients"
    __table_args__ = (
        # Composite FK (D24): the primary supplier must be a row of the
        # supplier_ingredients junction FOR THIS ingredient. Nullable column —
        # NULL means "no primary supplier assigned" and passes the FK.
        # use_alter breaks the circular creation order with the junction
        # table; the migration adds this constraint after both tables exist.
        ForeignKeyConstraint(
            ["primary_supplier_id", "id"],
            ["supplier_ingredients.supplier_id", "supplier_ingredients.ingredient_id"],
            name="fk_ingredients_primary_supplier",
            use_alter=True,
        ),
        CheckConstraint(
            f"status IN ('{STATUS_ACTIVE}', '{STATUS_ARCHIVED}')",
            name="ck_ingredients_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), nullable=False)
    min_safety_stock: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0, nullable=False)
    critical_threshold: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0, nullable=False)
    usage_per_serving: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=1, nullable=False)
    primary_supplier_id: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=STATUS_ACTIVE, nullable=False, index=True
    )

    unit: Mapped["Unit"] = relationship(lazy="joined")
    stock_state: Mapped["IngredientStockState"] = relationship(
        back_populates="ingredient", cascade="all, delete-orphan", uselist=False
    )
    supplier_links: Mapped[list["SupplierIngredient"]] = relationship(
        back_populates="ingredient",
        cascade="all, delete-orphan",
        foreign_keys="SupplierIngredient.ingredient_id",
    )


class IngredientStockState(Base):
    """The single canonical cached balance (D14) + confidence metadata."""

    __tablename__ = "ingredient_stock_state"
    __table_args__ = (
        CheckConstraint(
            f"confidence IN ('{CONFIDENCE_HIGH}', '{CONFIDENCE_MEDIUM}', '{CONFIDENCE_LOW}')",
            name="ck_stock_state_confidence",
        ),
    )

    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id"), primary_key=True
    )
    current_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0, nullable=False)
    last_counted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_counted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    last_transaction_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    unresolved_discrepancy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), default=CONFIDENCE_LOW, nullable=False)
    version: Mapped[int] = mapped_column(default=0, nullable=False)

    ingredient: Mapped[Ingredient] = relationship(back_populates="stock_state")
