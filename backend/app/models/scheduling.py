"""Supplier delivery schedules and calendar exceptions (Phase 2 Step 1).

Design (decisions D32-D35):
- Every rule belongs to ONE supplier; suppliers never share rules.
- ``ingredient_id NULL`` = the rule applies to every ingredient the supplier
  provides. A row WITH ``ingredient_id`` is an ADDITIVE override that applies
  only to that ingredient (D32). Exclusions are modeled by keeping shared
  days on supplier-wide rules and extra days on ingredient rules.
- Times are local wall-clock in the rule's timezone; the engine converts to
  UTC instants for all comparisons (D5/D16).
- Cutoffs use a days-before-delivery offset (1..6, D35), so the week the
  cutoff falls in is always unambiguous and stored data needs no week field.
- Exceptions are date-specific rows that never rewrite the weekly rules;
  they mark cancelled/suspended deliveries, move one cutoff, or add a
  one-time extra delivery (D34).
"""

from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    column,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin

# Supplier-level preferred contact method (general instructions).
CONTACT_METHODS = ("phone", "line", "email", "website", "other")

# Schedule-level required contact action (e.g. "call before Tuesday 11:00").
CONTACT_ACTIONS = ("call", "email", "line", "website_order", "other")

# Calendar exception types (D34). None of them edit weekly rules.
EXCEPTION_DELIVERY_CANCELLED = "delivery_cancelled"
EXCEPTION_ORDERING_SUSPENDED = "ordering_suspended"
EXCEPTION_CUTOFF_OVERRIDE = "cutoff_override"      # uses override_cutoff_at
EXCEPTION_EXTRA_DELIVERY = "extra_delivery"        # uses override_cutoff_at
EXCEPTION_TYPES = (
    EXCEPTION_DELIVERY_CANCELLED,
    EXCEPTION_ORDERING_SUSPENDED,
    EXCEPTION_CUTOFF_OVERRIDE,
    EXCEPTION_EXTRA_DELIVERY,
)

# Computed opportunity statuses (D33). Never stored.
STATUS_UPCOMING = "upcoming"            # ordering window not open yet
STATUS_OPEN = "open"                    # window open, cutoff (inclusive) not passed
STATUS_CUTOFF_REACHED = "cutoff_reached"  # cutoff passed, delivery still ahead
STATUS_MISSED = "missed"                # delivery date has passed
STATUS_UNAVAILABLE_HOLIDAY = "unavailable_holiday"  # exception blocks it
# "covered" deliberately does not exist until purchase orders land (Phase 2
# Step 2) — nothing may claim a deadline is covered before then.


class SupplierScheduleRule(TimestampMixin, Base):
    """One repeatable weekly delivery rule for one supplier."""

    __tablename__ = "supplier_schedule_rules"
    __table_args__ = (
        CheckConstraint("delivery_weekday BETWEEN 0 AND 6", name="ck_rules_weekday"),
        CheckConstraint(
            "order_cutoff_days_before BETWEEN 1 AND 6", name="ck_rules_cutoff_offset"
        ),
        CheckConstraint(
            "order_open_days_before IS NULL OR order_open_days_before BETWEEN 1 AND 13",
            name="ck_rules_open_offset",
        ),
        CheckConstraint(
            "reminder_days_before IS NULL OR reminder_days_before BETWEEN 0 AND 13",
            name="ck_rules_reminder_offset",
        ),
        CheckConstraint(
            "contact_deadline_days_before IS NULL OR contact_deadline_days_before BETWEEN 0 AND 13",
            name="ck_rules_contact_offset",
        ),
        CheckConstraint(
            "contact_action IS NULL OR contact_action IN ("
            + ", ".join(f"'{a}'" for a in CONTACT_ACTIONS)
            + ")",
            name="ck_rules_contact_action",
        ),
        # Duplicate prevention for ACTIVE rules (partial unique indexes —
        # supported by both SQLite and PostgreSQL): one supplier-wide rule
        # per delivery weekday, and one rule per (weekday, ingredient).
        Index(
            "uq_rules_supplier_weekday_active",
            "supplier_id", "delivery_weekday",
            unique=True,
            sqlite_where=column("ingredient_id").is_(None) & (column("is_active") == true()),
            postgresql_where=column("ingredient_id").is_(None) & (column("is_active") == true()),
        ),
        Index(
            "uq_rules_supplier_weekday_ingredient_active",
            "supplier_id", "delivery_weekday", "ingredient_id",
            unique=True,
            sqlite_where=column("ingredient_id").isnot(None) & (column("is_active") == true()),
            postgresql_where=column("ingredient_id").isnot(None) & (column("is_active") == true()),
        ),
        Index("ix_rules_supplier", "supplier_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id"), nullable=False, index=True
    )
    # NULL = supplier-wide rule; set = additive ingredient-specific override.
    ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredients.id"), nullable=True
    )
    delivery_weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon
    order_cutoff_days_before: Mapped[int] = mapped_column(Integer, nullable=False)
    order_cutoff_time: Mapped[time] = mapped_column(Time, nullable=False)
    order_open_days_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_open_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    reminder_days_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reminder_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Tokyo", nullable=False)
    contact_action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contact_deadline_days_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_deadline_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    contact_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    special_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    supplier: Mapped["Supplier"] = relationship(  # noqa: F821
        back_populates="schedule_rules"
    )
    ingredient: Mapped["Ingredient | None"] = relationship(  # noqa: F821
        foreign_keys=[ingredient_id]
    )


class SupplierCalendarException(TimestampMixin, Base):
    """A date-specific override that never edits the weekly rules (D34)."""

    __tablename__ = "supplier_calendar_exceptions"
    __table_args__ = (
        CheckConstraint(
            "type IN (" + ", ".join(f"'{t}'" for t in EXCEPTION_TYPES) + ")",
            name="ck_exceptions_type",
        ),
        # One exception of each type per supplier per date.
        Index(
            "uq_exceptions_supplier_date_type",
            "supplier_id", "date", "type",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id"), nullable=False, index=True
    )
    schedule_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_schedule_rules.id"), nullable=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    # cutoff_override: the replacement cutoff instant (UTC).
    # extra_delivery: the cutoff instant for that one-time delivery (UTC).
    override_cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    supplier: Mapped["Supplier"] = relationship(back_populates="calendar_exceptions")  # noqa: F821
    schedule_rule: Mapped["SupplierScheduleRule | None"] = relationship()
