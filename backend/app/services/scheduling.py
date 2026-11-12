"""Supplier delivery scheduling engine and administration (Phase 2 Step 1).

Core idea: every rule stores LOCAL wall-clock times plus a timezone; the
engine converts each (delivery date, offset, time) triple into a UTC instant
and compares instants only. Weekdays and dates are evaluated in the rule's
own timezone.

Statuses (D33):
- ``upcoming``: an order-opening window exists and has not opened yet.
- ``open``: ordering is possible now; cutoff is INCLUSIVE (D16), so
  ``now == cutoff`` still counts as open.
- ``cutoff_reached``: the cutoff has passed but the delivery is still ahead.
- ``missed``: the delivery date itself has passed.
- ``unavailable_holiday``: a calendar exception blocks this cycle.
``covered`` deliberately does not exist until purchase orders land.

Exceptions (D34) are date-specific rows that never rewrite weekly rules:
cancel/suspend a delivery, move one cutoff, or add a one-time delivery.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import (
    AuditLog,
    Ingredient,
    Supplier,
    SupplierCalendarException,
    SupplierIngredient,
    SupplierScheduleRule,
    User,
)
from app.models.scheduling import (
    EXCEPTION_CUTOFF_OVERRIDE,
    EXCEPTION_DELIVERY_CANCELLED,
    EXCEPTION_EXTRA_DELIVERY,
    EXCEPTION_ORDERING_SUSPENDED,
    STATUS_CUTOFF_REACHED,
    STATUS_MISSED,
    STATUS_OPEN,
    STATUS_UNAVAILABLE_HOLIDAY,
    STATUS_UPCOMING,
)
from app.models.supplier import STATUS_ACTIVE
from app.services.catalog import _audit
from app.services.inventory import _begin_write_transaction, _run_atomic

_RULE_FIELDS = (
    "ingredient_id",
    "delivery_weekday",
    "order_cutoff_days_before",
    "order_cutoff_time",
    "order_open_days_before",
    "order_open_time",
    "reminder_days_before",
    "reminder_time",
    "timezone",
    "contact_action",
    "contact_deadline_days_before",
    "contact_deadline_time",
    "contact_required",
    "special_instructions",
)


def _rule_zone(rule: SupplierScheduleRule) -> ZoneInfo:
    return ZoneInfo(rule.timezone)


def _validate_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ApiError(400, "invalid_timezone", f"Unknown timezone: {name!r}.")


def _instant(tz: ZoneInfo, day: date, clock: time) -> datetime:
    """A local wall-clock time on a local date, as a UTC instant."""
    return datetime.combine(day, clock, tzinfo=tz).astimezone(timezone.utc)


def _offset_instant(tz: ZoneInfo, delivery_date: date,
                    days_before: int, clock: time) -> datetime:
    return _instant(tz, delivery_date - timedelta(days=days_before), clock)


def rule_cycle_times(rule: SupplierScheduleRule, delivery_date: date) -> dict:
    """All UTC instants of one rule cycle for a concrete delivery date."""
    tz = _rule_zone(rule)
    times = {
        "cutoff_at": _offset_instant(
            tz, delivery_date, rule.order_cutoff_days_before, rule.order_cutoff_time
        ),
        "order_open_at": None,
        "reminder_at": None,
        "contact_deadline_at": None,
    }
    if rule.order_open_days_before is not None and rule.order_open_time is not None:
        times["order_open_at"] = _offset_instant(
            tz, delivery_date, rule.order_open_days_before, rule.order_open_time
        )
    if rule.reminder_days_before is not None and rule.reminder_time is not None:
        times["reminder_at"] = _offset_instant(
            tz, delivery_date, rule.reminder_days_before, rule.reminder_time
        )
    if (
        rule.contact_action is not None
        and rule.contact_deadline_days_before is not None
        and rule.contact_deadline_time is not None
    ):
        times["contact_deadline_at"] = _offset_instant(
            tz, delivery_date,
            rule.contact_deadline_days_before, rule.contact_deadline_time,
        )
    return times


# --- Validation ---------------------------------------------------------------

def _pair_error(field: str) -> ApiError:
    return ApiError(
        400, "incomplete_pair",
        f"{field} requires both a days-before offset and a time.",
    )


def _validate_rule_payload(db: Session, *, supplier: Supplier, values: dict,
                           exclude_rule_id: int | None = None) -> None:
    """Validate a fully merged rule payload (used for create AND update)."""
    _validate_timezone(values["timezone"])

    # Paired optional fields must come as complete pairs.
    for days_field, time_field, label in (
        ("order_open_days_before", "order_open_time", "Order opening"),
        ("reminder_days_before", "reminder_time", "Reminder"),
        ("contact_deadline_days_before", "contact_deadline_time", "Contact deadline"),
    ):
        if (values[days_field] is None) != (values[time_field] is None):
            raise _pair_error(label)

    # Reference delivery date far enough ahead: every offset (<=13 days)
    # stays in the past of this date, so comparisons are deterministic.
    reference_delivery = date(2026, 11, 16)  # a Monday
    cutoff = _offset_instant(
        _rule_zone_from_values(values), reference_delivery,
        values["order_cutoff_days_before"], values["order_cutoff_time"],
    )

    if values["order_open_days_before"] is not None:
        opened = _offset_instant(
            _rule_zone_from_values(values), reference_delivery,
            values["order_open_days_before"], values["order_open_time"],
        )
        if opened >= cutoff:
            raise ApiError(
                400, "invalid_order_window",
                "The order opening must be strictly before the cutoff.",
            )

    if values["reminder_days_before"] is not None:
        reminder = _offset_instant(
            _rule_zone_from_values(values), reference_delivery,
            values["reminder_days_before"], values["reminder_time"],
        )
        if reminder >= cutoff:
            raise ApiError(
                400, "invalid_reminder",
                "The reminder must be strictly before the cutoff.",
            )

    if values["contact_action"] is not None:
        if values["contact_deadline_days_before"] is None:
            raise ApiError(
                400, "incomplete_pair",
                "A contact action requires a deadline (days-before and time).",
            )
        delivery_start = _instant(
            _rule_zone_from_values(values), reference_delivery, time(0, 0)
        )
        contact_deadline = _offset_instant(
            _rule_zone_from_values(values), reference_delivery,
            values["contact_deadline_days_before"], values["contact_deadline_time"],
        )
        if contact_deadline >= delivery_start:
            raise ApiError(
                400, "invalid_contact_deadline",
                "The contact deadline must be before the delivery day.",
            )
    elif values["contact_required"]:
        raise ApiError(
            400, "invalid_contact_deadline",
            "A required contact action needs an action type.",
        )

    # Ingredient overrides must reference an ingredient the supplier provides.
    ingredient_id = values["ingredient_id"]
    if ingredient_id is not None:
        link = db.scalar(
            select(SupplierIngredient).where(
                SupplierIngredient.supplier_id == supplier.id,
                SupplierIngredient.ingredient_id == ingredient_id,
            )
        )
        if link is None:
            raise ApiError(
                400, "ingredient_not_supplied",
                "Ingredient-specific schedules require the supplier to be "
                "assigned to that ingredient first.",
            )

    # Duplicate ACTIVE rule: same supplier + weekday + ingredient (incl. NULL).
    stmt = select(SupplierScheduleRule).where(
        SupplierScheduleRule.supplier_id == supplier.id,
        SupplierScheduleRule.delivery_weekday == values["delivery_weekday"],
        SupplierScheduleRule.is_active.is_(True),
    )
    if ingredient_id is None:
        stmt = stmt.where(SupplierScheduleRule.ingredient_id.is_(None))
    else:
        stmt = stmt.where(SupplierScheduleRule.ingredient_id == ingredient_id)
    if exclude_rule_id is not None:
        stmt = stmt.where(SupplierScheduleRule.id != exclude_rule_id)
    if db.scalar(stmt) is not None:
        raise ApiError(
            409, "conflicting_rule",
            "An active schedule rule already exists for this supplier, "
            "weekday and ingredient.",
        )


def _rule_zone_from_values(values: dict) -> ZoneInfo:
    return ZoneInfo(values["timezone"])


def _rule_snapshot(rule: SupplierScheduleRule) -> dict:
    snap = {field: getattr(rule, field) for field in _RULE_FIELDS}
    for key, value in snap.items():
        if isinstance(value, time):
            snap[key] = value.strftime("%H:%M")
    snap["is_active"] = rule.is_active
    return snap


def _get_supplier(db: Session, supplier_id: int) -> Supplier:
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise ApiError(404, "supplier_not_found", "Supplier not found.")
    return supplier


def _get_rule(db: Session, rule_id: int) -> SupplierScheduleRule:
    rule = db.get(SupplierScheduleRule, rule_id)
    if rule is None:
        raise ApiError(404, "schedule_not_found", "Schedule rule not found.")
    return rule


# --- Rule CRUD ------------------------------------------------------------------

def create_rule(db: Session, *, admin: User, supplier_id: int, data) -> SupplierScheduleRule:
    _begin_write_transaction(db)

    def work() -> SupplierScheduleRule:
        supplier = _get_supplier(db, supplier_id)
        if supplier.status != STATUS_ACTIVE:
            raise ApiError(
                400, "supplier_inactive",
                "Schedules cannot be added to an archived supplier.",
            )
        values = {field: getattr(data, field) for field in _RULE_FIELDS}
        _validate_rule_payload(db, supplier=supplier, values=values)
        rule = SupplierScheduleRule(supplier_id=supplier.id, **values)
        db.add(rule)
        db.flush()
        _audit(db, admin, "schedule.create", "schedule_rule", rule.id, None,
               _rule_snapshot(rule))
        return rule

    return _run_atomic(db, work)


def update_rule(db: Session, *, admin: User, rule_id: int, data) -> SupplierScheduleRule:
    _begin_write_transaction(db)

    def work() -> SupplierScheduleRule:
        rule = _get_rule(db, rule_id)
        supplier = _get_supplier(db, rule.supplier_id)
        before = _rule_snapshot(rule)
        values = {field: getattr(rule, field) for field in _RULE_FIELDS}
        for field in _RULE_FIELDS:
            # Only explicitly provided fields change; explicit null clears.
            if field in data.model_fields_set:
                values[field] = getattr(data, field)
        _validate_rule_payload(
            db, supplier=supplier, values=values, exclude_rule_id=rule.id
        )
        for field in _RULE_FIELDS:
            setattr(rule, field, values[field])
        _audit(db, admin, "schedule.update", "schedule_rule", rule.id, before,
               _rule_snapshot(rule))
        return rule

    return _run_atomic(db, work)


def set_rule_active(db: Session, *, admin: User, rule_id: int,
                    active: bool, action: str) -> SupplierScheduleRule:
    _begin_write_transaction(db)

    def work() -> SupplierScheduleRule:
        rule = _get_rule(db, rule_id)
        if rule.is_active == active:
            return rule  # idempotent
        before = _rule_snapshot(rule)
        if active:
            # Reactivating must not collide with a newer active rule.
            supplier = _get_supplier(db, rule.supplier_id)
            values = {field: getattr(rule, field) for field in _RULE_FIELDS}
            _validate_rule_payload(
                db, supplier=supplier, values=values, exclude_rule_id=rule.id
            )
        rule.is_active = active
        _audit(db, admin, action, "schedule_rule", rule.id, before,
               _rule_snapshot(rule))
        return rule

    return _run_atomic(db, work)


# --- Calendar exceptions --------------------------------------------------------

def _exception_snapshot(exc: SupplierCalendarException) -> dict:
    return {
        "date": exc.date.isoformat(),
        "type": exc.type,
        "schedule_rule_id": exc.schedule_rule_id,
        "override_cutoff_at": (
            exc.override_cutoff_at.isoformat() if exc.override_cutoff_at else None
        ),
        "note": exc.note,
        "is_active": exc.is_active,
    }


def _validate_exception(db: Session, *, supplier: Supplier, exc_type: str,
                        schedule_rule_id: int | None,
                        override_cutoff_at: datetime | None) -> datetime | None:
    if schedule_rule_id is not None:
        rule = db.get(SupplierScheduleRule, schedule_rule_id)
        if rule is None or rule.supplier_id != supplier.id:
            raise ApiError(
                400, "schedule_not_found",
                "The exception must reference a schedule rule of this supplier.",
            )
    if exc_type in (EXCEPTION_CUTOFF_OVERRIDE, EXCEPTION_EXTRA_DELIVERY):
        if override_cutoff_at is None:
            raise ApiError(
                400, "missing_override_cutoff",
                f"An '{exc_type}' exception requires override_cutoff_at.",
            )
        if override_cutoff_at.tzinfo is None:
            raise ApiError(
                400, "naive_datetime",
                "override_cutoff_at must include a timezone offset.",
            )
        return override_cutoff_at.astimezone(timezone.utc)
    return None


def create_exception(db: Session, *, admin: User, supplier_id: int,
                     data) -> SupplierCalendarException:
    _begin_write_transaction(db)

    def work() -> SupplierCalendarException:
        supplier = _get_supplier(db, supplier_id)
        normalized_cutoff = _validate_exception(
            db, supplier=supplier, exc_type=data.type,
            schedule_rule_id=data.schedule_rule_id,
            override_cutoff_at=data.override_cutoff_at,
        )
        clash = db.scalar(
            select(SupplierCalendarException).where(
                SupplierCalendarException.supplier_id == supplier.id,
                SupplierCalendarException.date == data.date,
                SupplierCalendarException.type == data.type,
            )
        )
        if clash is not None:
            raise ApiError(
                409, "duplicate_exception",
                "An exception of this type already exists for that date.",
            )
        exc = SupplierCalendarException(
            supplier_id=supplier.id,
            schedule_rule_id=data.schedule_rule_id,
            date=data.date,
            type=data.type,
            override_cutoff_at=normalized_cutoff,
            note=data.note,
            is_active=True,
        )
        db.add(exc)
        db.flush()
        _audit(db, admin, "schedule_exception.create", "schedule_exception",
               exc.id, None, _exception_snapshot(exc))
        return exc

    return _run_atomic(db, work)


def _get_exception(db: Session, exception_id: int) -> SupplierCalendarException:
    exc = db.get(SupplierCalendarException, exception_id)
    if exc is None:
        raise ApiError(404, "exception_not_found", "Calendar exception not found.")
    return exc


def update_exception(db: Session, *, admin: User, exception_id: int,
                     data) -> SupplierCalendarException:
    _begin_write_transaction(db)

    def work() -> SupplierCalendarException:
        exc = _get_exception(db, exception_id)
        supplier = _get_supplier(db, exc.supplier_id)
        before = _exception_snapshot(exc)
        fields = data.model_fields_set
        new_date = data.date if "date" in fields and data.date is not None else exc.date
        new_rule_id = (
            data.schedule_rule_id if "schedule_rule_id" in fields
            else exc.schedule_rule_id
        )
        new_override = (
            data.override_cutoff_at if "override_cutoff_at" in fields
            else (_as_utc(exc.override_cutoff_at) if exc.override_cutoff_at else None)
        )
        if "note" in fields:
            exc.note = data.note
        normalized = _validate_exception(
            db, supplier=supplier, exc_type=exc.type,
            schedule_rule_id=new_rule_id, override_cutoff_at=new_override,
        )
        clash = db.scalar(
            select(SupplierCalendarException).where(
                SupplierCalendarException.supplier_id == supplier.id,
                SupplierCalendarException.date == new_date,
                SupplierCalendarException.type == exc.type,
                SupplierCalendarException.id != exc.id,
            )
        )
        if clash is not None:
            raise ApiError(
                409, "duplicate_exception",
                "An exception of this type already exists for that date.",
            )
        exc.date = new_date
        exc.schedule_rule_id = new_rule_id
        exc.override_cutoff_at = normalized
        _audit(db, admin, "schedule_exception.update", "schedule_exception",
               exc.id, before, _exception_snapshot(exc))
        return exc

    return _run_atomic(db, work)


def set_exception_active(db: Session, *, admin: User, exception_id: int,
                         active: bool, action: str) -> SupplierCalendarException:
    _begin_write_transaction(db)

    def work() -> SupplierCalendarException:
        exc = _get_exception(db, exception_id)
        if exc.is_active == active:
            return exc  # idempotent
        before = _exception_snapshot(exc)
        exc.is_active = active
        _audit(db, admin, action, "schedule_exception", exc.id, before,
               _exception_snapshot(exc))
        return exc

    return _run_atomic(db, work)


# --- Scheduling engine ------------------------------------------------------------

def _as_utc(moment: datetime) -> datetime:
    """SQLite returns naive datetimes; treat stored values as UTC (D5)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _cycle_status(now: datetime, today: date, delivery_date: date,
                  cutoff_at: datetime, order_open_at: datetime | None) -> str:
    if delivery_date < today:
        return STATUS_MISSED
    if now > cutoff_at:  # cutoff is INCLUSIVE: now == cutoff is still open (D16)
        return STATUS_CUTOFF_REACHED
    if order_open_at is not None and now < order_open_at:
        return STATUS_UPCOMING
    return STATUS_OPEN


def compute_opportunities(db: Session, *, supplier_id: int, now_utc: datetime,
                          horizon_days: int = 21,
                          business_tz: str = "Asia/Tokyo") -> list[dict]:
    """Upcoming (and recent) delivery/order cycles for one supplier.

    ``now_utc`` is injectable so tests can pin the clock. Returned cycles
    span from one week back (so ``missed`` stays visible) to the horizon.
    Extra deliveries carry no weekly rule, so their "today" baseline uses
    the business timezone.
    """
    supplier = _get_supplier(db, supplier_id)
    now_utc = _as_utc(now_utc)

    rules = db.scalars(
        select(SupplierScheduleRule).where(
            SupplierScheduleRule.supplier_id == supplier.id,
            SupplierScheduleRule.is_active.is_(True),
        )
    ).all()
    exceptions = db.scalars(
        select(SupplierCalendarException).where(
            SupplierCalendarException.supplier_id == supplier.id,
            SupplierCalendarException.is_active.is_(True),
        )
    ).all()
    blocked: dict[date, SupplierCalendarException] = {}
    cutoff_overrides: dict[date, SupplierCalendarException] = {}
    extras: list[SupplierCalendarException] = []
    for exc in exceptions:
        if exc.type in (EXCEPTION_DELIVERY_CANCELLED, EXCEPTION_ORDERING_SUSPENDED):
            blocked.setdefault(exc.date, exc)
        elif exc.type == EXCEPTION_CUTOFF_OVERRIDE:
            cutoff_overrides.setdefault(exc.date, exc)
        elif exc.type == EXCEPTION_EXTRA_DELIVERY:
            extras.append(exc)

    ingredient_names = {
        ingredient.id: ingredient.name
        for ingredient in db.scalars(
            select(Ingredient).where(
                Ingredient.id.in_(
                    [r.ingredient_id for r in rules if r.ingredient_id is not None]
                )
            )
        ).all()
    } if any(r.ingredient_id is not None for r in rules) else {}

    opportunities: list[dict] = []
    for rule in rules:
        tz = _rule_zone(rule)
        today = now_utc.astimezone(tz).date()
        first = today - timedelta(days=7)
        last = today + timedelta(days=horizon_days)
        day = first
        while day <= last:
            if day.weekday() != rule.delivery_weekday:
                day += timedelta(days=1)
                continue
            times = rule_cycle_times(rule, day)
            exception_info = None
            if day in blocked:
                status = STATUS_UNAVAILABLE_HOLIDAY
                exception_info = {"type": blocked[day].type, "note": blocked[day].note}
            else:
                if day in cutoff_overrides:
                    override = cutoff_overrides[day]
                    times["cutoff_at"] = _as_utc(override.override_cutoff_at)
                    exception_info = {"type": override.type, "note": override.note}
                status = _cycle_status(
                    now_utc, today, day, times["cutoff_at"], times["order_open_at"]
                )
            contact = None
            if times["contact_deadline_at"] is not None:
                contact = {
                    "action": rule.contact_action,
                    "deadline_at": times["contact_deadline_at"],
                    "required": rule.contact_required,
                }
            opportunities.append({
                "rule_id": rule.id,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "ingredient_id": rule.ingredient_id,
                "ingredient_name": ingredient_names.get(rule.ingredient_id),
                "delivery_date": day,
                "delivery_weekday": rule.delivery_weekday,
                "cutoff_at": times["cutoff_at"],
                "order_open_at": times["order_open_at"],
                "reminder_at": times["reminder_at"],
                "status": status,
                "special_instructions": rule.special_instructions,
                "contact": contact,
                "exception": exception_info,
                "timezone": rule.timezone,
            })
            day += timedelta(days=1)

    today_business = now_utc.astimezone(ZoneInfo(business_tz)).date()
    for exc in extras:
        override = cutoff_overrides.get(exc.date)
        times_cutoff = _as_utc(override.override_cutoff_at if override else exc.override_cutoff_at)
        exception_info = {"type": exc.type, "note": exc.note}
        if exc.date in blocked:
            status = STATUS_UNAVAILABLE_HOLIDAY
            exception_info = {"type": blocked[exc.date].type, "note": blocked[exc.date].note}
        else:
            status = _cycle_status(now_utc, today_business, exc.date, times_cutoff, None)
        opportunities.append({
            "rule_id": exc.schedule_rule_id,
            "supplier_id": supplier.id,
            "supplier_name": supplier.name,
            "ingredient_id": None,
            "ingredient_name": None,
            "delivery_date": exc.date,
            "delivery_weekday": exc.date.weekday(),
            "cutoff_at": times_cutoff,
            "order_open_at": None,
            "reminder_at": None,
            "status": status,
            "special_instructions": exc.note,
            "contact": None,
            "exception": exception_info,
            "timezone": "UTC",
        })

    opportunities.sort(key=lambda o: o["cutoff_at"])
    return opportunities
