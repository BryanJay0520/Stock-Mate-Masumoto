"""Suppliers and the ingredient-supplier junction (decisions D11, D19, D24).

``SupplierIngredient`` is the ONLY place that records "supplier X provides
ingredient Y". ``Ingredient.primary_supplier_id`` merely points at one of
these rows — enforced by a composite foreign key (D24).

The junction references ``ingredients`` while ``ingredients`` references the
junction back: a deliberate circular relationship. It is created safely in
two steps by the migration (tables first, circular FK added afterwards), so
it works identically on SQLite and PostgreSQL.
"""

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

# Preferred contact method for general ordering instructions (Phase 2 Step 1).
CONTACT_METHODS = ("phone", "line", "email", "website", "other")


class Supplier(TimestampMixin, Base):
    """Archived, never deleted: historical orders must keep their supplier."""

    __tablename__ = "suppliers"
    __table_args__ = (
        CheckConstraint(
            f"status IN ('{STATUS_ACTIVE}', '{STATUS_ARCHIVED}')",
            name="ck_suppliers_status",
        ),
        CheckConstraint(
            "contact_method IS NULL OR contact_method IN ("
            + ", ".join(f"'{m}'" for m in CONTACT_METHODS)
            + ")",
            name="ck_suppliers_contact_method",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # General ordering instructions (Phase 2 Step 1, D34).
    contact_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ordering_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ordering_procedures: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_ACTIVE, nullable=False)

    ingredient_links: Mapped[list["SupplierIngredient"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan"
    )
    schedule_rules: Mapped[list["SupplierScheduleRule"]] = relationship(  # noqa: F821
        back_populates="supplier"
    )
    calendar_exceptions: Mapped[list["SupplierCalendarException"]] = relationship(  # noqa: F821
        back_populates="supplier"
    )


class SupplierIngredient(Base):
    """Junction: which ingredients a supplier can provide (D19).

    The composite primary key doubles as the unique constraint and as the
    target of ingredients' composite foreign key (D24).
    """

    __tablename__ = "supplier_ingredients"

    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id"), primary_key=True
    )
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id"), primary_key=True, index=True
    )
    package_size: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    purchase_package_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    whole_packages_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False,
                                                      server_default="0")
    min_order_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)

    supplier: Mapped[Supplier] = relationship(back_populates="ingredient_links")
    ingredient: Mapped["Ingredient"] = relationship(  # noqa: F821 (resolved by registry)
        back_populates="supplier_links", foreign_keys=[ingredient_id]
    )
