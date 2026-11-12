"""Persistent in-app notices and account-level interaction state (D43)."""

from datetime import date, datetime, time

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

TYPES = ("daily_count", "order_cutoff", "supplier_contact", "forecast_shortage", "verify_pending_order", "pending_delivery")
PRIORITIES = ("critical", "high", "normal", "informational")
STATUSES = ("active", "expired", "superseded", "resolved")


class NotificationEvent(Base):
    __tablename__ = "notification_events"
    __table_args__ = (
        CheckConstraint("type IN (" + ", ".join(f"'{x}'" for x in TYPES) + ")", name="ck_notification_type"),
        CheckConstraint("priority IN (" + ", ".join(f"'{x}'" for x in PRIORITIES) + ")", name="ck_notification_priority"),
        CheckConstraint("status IN (" + ", ".join(f"'{x}'" for x in STATUSES) + ")", name="ck_notification_status"),
        Index("ix_notification_status_action", "status", "action_at"),
        Index("ix_notification_business_date", "business_date"),
        Index("ix_notification_condition_key", "condition_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    condition_key: Mapped[str] = mapped_column(String(190), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(220), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Snapshot of plain operational details, never credentials or auth tokens.
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredients.id"), nullable=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    delivery_cycle_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    business_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    navigation_target: Mapped[str] = mapped_column(String(220), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationUserState(Base):
    __tablename__ = "notification_user_states"
    __table_args__ = (UniqueConstraint("notification_id", "user_id", name="uq_notification_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("notification_events.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationSetting(Base):
    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    daily_count_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    daily_count_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(21, 30))
    order_cutoff_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supplier_contact_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    forecast_shortage_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    verify_pending_order_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pending_delivery_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationCountRule(Base):
    __tablename__ = "notification_count_rules"

    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
