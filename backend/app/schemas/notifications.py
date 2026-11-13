"""Notification API contracts; timestamps are UTC and quantities are snapshots."""

from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class NotificationOut(BaseModel):
    id: int
    type: str
    priority: str
    status: str
    title: str
    message: str
    details: dict[str, Any]
    ingredient_id: int | None
    supplier_id: int | None
    purchase_order_id: int | None
    delivery_cycle_key: str | None
    business_date: date | None
    action_at: datetime | None
    navigation_target: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    resolved_at: datetime | None
    read_at: datetime | None
    acknowledged_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    total: int
    limit: int
    offset: int
    evaluated_at: datetime | None


class NotificationSettingsOut(BaseModel):
    daily_count_enabled: bool
    daily_count_time: time
    daily_count_ingredient_ids: list[int]
    order_cutoff_enabled: bool
    supplier_contact_enabled: bool
    forecast_shortage_enabled: bool
    verify_pending_order_enabled: bool
    pending_delivery_enabled: bool
    automatic_evaluation_enabled: bool
    evaluation_interval_seconds: int


class NotificationSettingsUpdate(BaseModel):
    daily_count_enabled: bool | None = None
    daily_count_time: time | None = None
    daily_count_ingredient_ids: list[int] | None = Field(default=None, max_length=1000)
    order_cutoff_enabled: bool | None = None
    supplier_contact_enabled: bool | None = None
    forecast_shortage_enabled: bool | None = None
    verify_pending_order_enabled: bool | None = None
    pending_delivery_enabled: bool | None = None
    automatic_evaluation_enabled: bool | None = None
    evaluation_interval_seconds: int | None = Field(default=None, ge=60, le=900)

    @field_validator("daily_count_time")
    @classmethod
    def local_time_only(cls, value):
        if value is not None and value.tzinfo is not None:
            raise ValueError("Enter a local business time without a timezone offset.")
        return value


class EvaluationOut(BaseModel):
    created: int
    updated: int
    transitioned: int
    resolved: int = 0
    expired: int = 0
    superseded: int = 0
    evaluated_at: datetime
    business_date: date
    active_conditions: int


class SchedulerHealthOut(BaseModel):
    automatic_evaluation_enabled: bool
    evaluation_interval_seconds: int
    status: Literal["disabled", "unknown", "running", "stopped", "error"]
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error_code: str | None
    next_expected_evaluation_at: datetime | None
    last_result: dict[str, Any] | None


NotificationView = Literal["active", "all", "unread", "urgent", "history"]
