"""Supplier delivery scheduling API (Phase 2 Step 1).

Staff get read-only access to schedules, exceptions and computed ordering
opportunities; mutations are admin-only + CSRF-protected and audited.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import csrf_protect, get_current_user, require_admin
from app.database import get_db
from app.errors import ApiError
from app.models import Supplier, SupplierCalendarException, SupplierScheduleRule, User
from app.schemas.scheduling import (
    ExceptionIn,
    ExceptionOut,
    ExceptionUpdate,
    OpportunityOut,
    ScheduleRuleIn,
    ScheduleRuleOut,
    ScheduleRuleUpdate,
)
from app.services import scheduling

router = APIRouter()


def _rule_out(db: Session, rule: SupplierScheduleRule) -> ScheduleRuleOut:
    out = ScheduleRuleOut.model_validate(rule)
    if rule.ingredient_id is not None and rule.ingredient is not None:
        out.ingredient_name = rule.ingredient.name
    return out


def _get_supplier_or_404(db: Session, supplier_id: int) -> Supplier:
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise ApiError(404, "supplier_not_found", "Supplier not found.")
    return supplier


# --- Schedule rules -------------------------------------------------------------

@router.get("/suppliers/{supplier_id}/schedules",
            response_model=list[ScheduleRuleOut])
def list_rules(
    supplier_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_supplier_or_404(db, supplier_id)
    rules = db.scalars(
        select(SupplierScheduleRule)
        .where(SupplierScheduleRule.supplier_id == supplier_id)
        .order_by(
            SupplierScheduleRule.delivery_weekday, SupplierScheduleRule.id
        )
    ).all()
    return [_rule_out(db, rule) for rule in rules]


@router.post(
    "/suppliers/{supplier_id}/schedules", status_code=201,
    response_model=ScheduleRuleOut, dependencies=[Depends(csrf_protect)],
)
def create_rule(
    supplier_id: int,
    payload: ScheduleRuleIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rule = scheduling.create_rule(db, admin=admin, supplier_id=supplier_id, data=payload)
    return _rule_out(db, rule)


@router.patch(
    "/schedules/{rule_id}", response_model=ScheduleRuleOut,
    dependencies=[Depends(csrf_protect)],
)
def update_rule(
    rule_id: int,
    payload: ScheduleRuleUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rule = scheduling.update_rule(db, admin=admin, rule_id=rule_id, data=payload)
    return _rule_out(db, rule)


@router.post(
    "/schedules/{rule_id}/deactivate", response_model=ScheduleRuleOut,
    dependencies=[Depends(csrf_protect)],
)
def deactivate_rule(
    rule_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rule = scheduling.set_rule_active(
        db, admin=admin, rule_id=rule_id, active=False, action="schedule.deactivate"
    )
    return _rule_out(db, rule)


@router.post(
    "/schedules/{rule_id}/reactivate", response_model=ScheduleRuleOut,
    dependencies=[Depends(csrf_protect)],
)
def reactivate_rule(
    rule_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rule = scheduling.set_rule_active(
        db, admin=admin, rule_id=rule_id, active=True, action="schedule.reactivate"
    )
    return _rule_out(db, rule)


# --- Calendar exceptions ----------------------------------------------------------

@router.get("/suppliers/{supplier_id}/exceptions",
            response_model=list[ExceptionOut])
def list_exceptions(
    supplier_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_supplier_or_404(db, supplier_id)
    return db.scalars(
        select(SupplierCalendarException)
        .where(SupplierCalendarException.supplier_id == supplier_id)
        .order_by(SupplierCalendarException.date)
    ).all()


@router.post(
    "/suppliers/{supplier_id}/exceptions", status_code=201,
    response_model=ExceptionOut, dependencies=[Depends(csrf_protect)],
)
def create_exception(
    supplier_id: int,
    payload: ExceptionIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return scheduling.create_exception(
        db, admin=admin, supplier_id=supplier_id, data=payload
    )


@router.patch(
    "/exceptions/{exception_id}", response_model=ExceptionOut,
    dependencies=[Depends(csrf_protect)],
)
def update_exception(
    exception_id: int,
    payload: ExceptionUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return scheduling.update_exception(
        db, admin=admin, exception_id=exception_id, data=payload
    )


@router.post(
    "/exceptions/{exception_id}/deactivate", response_model=ExceptionOut,
    dependencies=[Depends(csrf_protect)],
)
def deactivate_exception(
    exception_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return scheduling.set_exception_active(
        db, admin=admin, exception_id=exception_id, active=False,
        action="schedule_exception.deactivate",
    )


@router.post(
    "/exceptions/{exception_id}/reactivate", response_model=ExceptionOut,
    dependencies=[Depends(csrf_protect)],
)
def reactivate_exception(
    exception_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return scheduling.set_exception_active(
        db, admin=admin, exception_id=exception_id, active=True,
        action="schedule_exception.reactivate",
    )


# --- Computed ordering opportunities ------------------------------------------------

@router.get("/suppliers/{supplier_id}/opportunities",
            response_model=list[OpportunityOut])
def supplier_opportunities(
    supplier_id: int,
    request: Request,
    days: int = Query(default=21, ge=1, le=90),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    return scheduling.compute_opportunities(
        db, supplier_id=supplier_id, now_utc=datetime.now(timezone.utc),
        horizon_days=days, business_tz=settings.business_timezone,
    )


@router.get("/ordering/opportunities", response_model=list[OpportunityOut])
def all_opportunities(
    request: Request,
    days: int = Query(default=14, ge=1, le=90),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    supplier_ids = db.scalars(
        select(Supplier.id).where(Supplier.status == "active")
    ).all()
    opportunities = []
    for supplier_id in supplier_ids:
        opportunities.extend(scheduling.compute_opportunities(
            db, supplier_id=supplier_id, now_utc=now, horizon_days=days,
            business_tz=settings.business_timezone,
        ))
    opportunities.sort(key=lambda o: o["cutoff_at"])
    return opportunities
