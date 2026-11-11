"""Inventory ledger API.

- GET  /inventory/stock                     current balances (staff+)
- POST /inventory/entries                   record remaining/used stock (staff+, CSRF)
- GET  /inventory/transactions              immutable history (staff+)
- POST /inventory/transactions/{id}/correct correct a mistaken entry (own/admin, CSRF)
- GET  /inventory/verify                    ledger ↔ cache check (admin)
- POST /inventory/verify/repair             repair cache FROM ledger (admin, CSRF)

All stock math lives in app/services/inventory.py — this module only handles
HTTP concerns (auth, CSRF, request/response mapping). Incoming/projected
quantities on /inventory/stock come from app/services/ordering.py (D36).
"""

import json
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import csrf_protect, get_current_user, require_admin
from app.database import get_db
from app.models import Ingredient, IngredientStockState, InventoryTransaction, Unit, User
from app.schemas.inventory import (
    CorrectionRequest,
    LedgerCheckRow,
    LedgerRepairResponse,
    LedgerVerifyResponse,
    StockEntryRequest,
    StockEntryResponse,
    StockOut,
    TransactionOut,
)
from app.services import inventory as ledger
from app.services import ordering

router = APIRouter()


def _to_out(transaction: InventoryTransaction) -> TransactionOut:
    return TransactionOut(
        id=transaction.id,
        ingredient_id=transaction.ingredient_id,
        type=transaction.type,
        quantity_delta=transaction.quantity_delta,
        resulting_quantity=transaction.resulting_quantity,
        previous_quantity=transaction.resulting_quantity - transaction.quantity_delta,
        reason=transaction.reason,
        mode=transaction.mode,
        entered_by=transaction.entered_by,
        business_date=transaction.business_date,
        created_at=transaction.created_at,
        idempotency_key=transaction.idempotency_key,
        supersedes_id=transaction.supersedes_id,
        counting_breakdown=json.loads(transaction.counting_json)
        if transaction.counting_json else None,
    )


@router.get("/inventory/stock", response_model=list[StockOut])
def current_stock(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = db.execute(
        select(Ingredient, IngredientStockState, Unit)
        .join(IngredientStockState, IngredientStockState.ingredient_id == Ingredient.id)
        .join(Unit, Ingredient.unit_id == Unit.id)
        .order_by(Ingredient.name)
    ).all()
    # Pending purchase-order supply, computed server-side (D36).
    incoming = ordering.incoming_by_ingredient(db)
    result = []
    for ingredient, state, unit in rows:
        bucket = incoming.get(ingredient.id)
        result.append(StockOut(
            ingredient_id=ingredient.id,
            ingredient_name=ingredient.name,
            category=ingredient.category,
            unit=unit.abbreviation,
            status=ingredient.status,
            current_quantity=state.current_quantity,
            last_counted_at=state.last_counted_at,
            last_counted_by=state.last_counted_by,
            last_transaction_at=state.last_transaction_at,
            confidence=state.confidence,
            count_package_enabled=ingredient.count_package_enabled,
            count_package_name=ingredient.count_package_name,
            count_units_per_package=ingredient.count_units_per_package,
            count_package_version=ingredient.count_package_version,
            incoming_quantity=bucket["incoming_quantity"] if bucket else Decimal("0.000"),
            incoming_ordered_quantity=(
                bucket["incoming_ordered_quantity"] if bucket else Decimal("0.000")
            ),
            incoming_confirmed_quantity=(
                bucket["incoming_confirmed_quantity"] if bucket else Decimal("0.000")
            ),
            expected_delivery=bucket["expected_delivery"] if bucket else None,
            incoming_status=bucket["incoming_status"] if bucket else None,
            projected_quantity=ordering.projected_quantity(
                state.current_quantity, bucket
            ),
        ))
    return result


@router.post(
    "/inventory/entries",
    response_model=StockEntryResponse,
    dependencies=[Depends(csrf_protect)],
)
def record_entry(
    payload: StockEntryRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    transaction, replayed = ledger.record_stock_entry(
        db,
        ingredient_id=payload.ingredient_id,
        user=user,
        mode=payload.mode,
        quantity=payload.quantity,
        package_count=payload.package_count,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        confirmed=payload.confirmed,
        allow_negative=payload.allow_negative,
        settings=settings,
    )
    return StockEntryResponse(
        transaction=_to_out(transaction), idempotent_replay=replayed
    )


@router.get("/inventory/transactions", response_model=list[TransactionOut])
def transaction_history(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ingredient_id: int | None = None,
    type: str | None = None,
    business_date: date | None = None,
    limit: int = Query(default=100, le=500),
):
    stmt = select(InventoryTransaction).order_by(InventoryTransaction.id.desc()).limit(limit)
    if ingredient_id is not None:
        stmt = stmt.where(InventoryTransaction.ingredient_id == ingredient_id)
    if type is not None:
        stmt = stmt.where(InventoryTransaction.type == type)
    if business_date is not None:
        stmt = stmt.where(InventoryTransaction.business_date == business_date)
    return [_to_out(row) for row in db.scalars(stmt).all()]


@router.post(
    "/inventory/transactions/{transaction_id}/correct",
    response_model=StockEntryResponse,
    dependencies=[Depends(csrf_protect)],
)
def correct_entry(
    transaction_id: int,
    payload: CorrectionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    correction, replayed = ledger.correct_transaction(
        db,
        transaction_id=transaction_id,
        user=user,
        corrected_quantity=payload.corrected_quantity,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        confirmed=payload.confirmed,
        settings=settings,
    )
    return StockEntryResponse(
        transaction=_to_out(correction), idempotent_replay=replayed
    )


@router.get("/inventory/verify", response_model=LedgerVerifyResponse)
def verify(
    _admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    rows = ledger.verify_ledger(db)
    return LedgerVerifyResponse(
        consistent=all(row["consistent"] for row in rows),
        rows=[LedgerCheckRow(**row) for row in rows],
    )


@router.post(
    "/inventory/verify/repair",
    response_model=LedgerRepairResponse,
    dependencies=[Depends(csrf_protect)],
)
def repair(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    report = ledger.repair_ledger(db, admin=admin)
    return LedgerRepairResponse(**report)
