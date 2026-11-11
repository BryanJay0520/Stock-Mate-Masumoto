"""Request/response schemas for supplier delivery scheduling (Phase 2 Step 1).

Local times (cutoff, opening, reminder, contact deadline) are wall-clock
``HH:MM`` values in the rule's timezone. Computed instants in responses
(cutoff_at, order_open_at, reminder_at, contact_deadline_at) are UTC
ISO-8601 datetimes — clients convert for display.
"""

from datetime import date, datetime, time
from datetime import date as date_cls  # alias: field named ``date`` shadows it
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ContactAction = Literal["call", "email", "line", "website_order", "other"]
ExceptionType = Literal[
    "delivery_cancelled", "ordering_suspended", "cutoff_override", "extra_delivery"
]


class ScheduleRuleIn(BaseModel):
    ingredient_id: int | None = None  # null = supplier-wide rule (D32)
    delivery_weekday: int = Field(ge=0, le=6)  # 0 = Monday
    order_cutoff_days_before: int = Field(ge=1, le=6)
    order_cutoff_time: time
    order_open_days_before: int | None = Field(default=None, ge=1, le=13)
    order_open_time: time | None = None
    reminder_days_before: int | None = Field(default=None, ge=0, le=13)
    reminder_time: time | None = None
    timezone: str = Field(default="Asia/Tokyo", max_length=50)
    contact_action: ContactAction | None = None
    contact_deadline_days_before: int | None = Field(default=None, ge=0, le=13)
    contact_deadline_time: time | None = None
    contact_required: bool = False
    special_instructions: str | None = None


class ScheduleRuleUpdate(BaseModel):
    """Only fields explicitly present in the payload are changed; explicit
    null clears an optional field."""

    ingredient_id: int | None = None
    delivery_weekday: int | None = Field(default=None, ge=0, le=6)
    order_cutoff_days_before: int | None = Field(default=None, ge=1, le=6)
    order_cutoff_time: time | None = None
    order_open_days_before: int | None = Field(default=None, ge=1, le=13)
    order_open_time: time | None = None
    reminder_days_before: int | None = Field(default=None, ge=0, le=13)
    reminder_time: time | None = None
    timezone: str | None = Field(default=None, max_length=50)
    contact_action: ContactAction | None = None
    contact_deadline_days_before: int | None = Field(default=None, ge=0, le=13)
    contact_deadline_time: time | None = None
    contact_required: bool | None = None
    special_instructions: str | None = None


class ScheduleRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_id: int
    ingredient_id: int | None
    ingredient_name: str | None = None
    delivery_weekday: int
    order_cutoff_days_before: int
    order_cutoff_time: time
    order_open_days_before: int | None
    order_open_time: time | None
    reminder_days_before: int | None
    reminder_time: time | None
    timezone: str
    contact_action: str | None
    contact_deadline_days_before: int | None
    contact_deadline_time: time | None
    contact_required: bool
    special_instructions: str | None
    is_active: bool


class ExceptionIn(BaseModel):
    date: date
    type: ExceptionType
    schedule_rule_id: int | None = None
    override_cutoff_at: datetime | None = None  # required for cutoff_override / extra_delivery
    note: str | None = None


class ExceptionUpdate(BaseModel):
    date: date_cls | None = None
    schedule_rule_id: int | None = None
    override_cutoff_at: datetime | None = None
    note: str | None = None


class ExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_id: int
    schedule_rule_id: int | None
    date: date
    type: str
    override_cutoff_at: datetime | None
    note: str | None
    is_active: bool


class ContactRequirementOut(BaseModel):
    action: str
    deadline_at: datetime  # UTC
    required: bool


class ExceptionInfoOut(BaseModel):
    type: str
    note: str | None


class OpportunityOut(BaseModel):
    """One concrete upcoming (or recent) delivery/order cycle."""

    rule_id: int | None  # null for one-time extra deliveries
    supplier_id: int
    supplier_name: str
    ingredient_id: int | None
    ingredient_name: str | None
    delivery_date: date
    delivery_weekday: int
    cutoff_at: datetime  # UTC; inclusive boundary (D16)
    order_open_at: datetime | None
    reminder_at: datetime | None
    status: str  # upcoming | open | cutoff_reached | missed | unavailable_holiday
    special_instructions: str | None
    contact: ContactRequirementOut | None
    exception: ExceptionInfoOut | None
    timezone: str
