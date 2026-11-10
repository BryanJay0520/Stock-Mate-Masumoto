"""The inventory ledger service (decisions D2, D3, D6, D14, D15, D23).

THE most correctness-critical code in StockMate. Rules:

- Stock only changes here, always as: one immutable ledger row + one atomic
  conditional UPDATE of the cached balance, committed together.
- On SQLite every transaction runs as BEGIN IMMEDIATE (see app/database.py),
  which serializes writers — two staff entering stock simultaneously can
  never lose an update. No SELECT FOR UPDATE anywhere.
- Idempotency keys make retries and double-taps harmless: a replayed
  submission returns the ORIGINAL transaction without re-applying anything.
- Corrections never edit history; they append a compensating entry.
"""

import time
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import (
    AuditLog,
    Ingredient,
    IngredientStockState,
    InventoryTransaction,
    User,
)
from app.models.ingredient import STATUS_ACTIVE
from app.models.inventory import (
    MODE_REMAINING,
    MODE_USED,
    TYPE_CONSUMPTION,
    TYPE_CORRECTION,
    TYPE_RECONCILIATION,
)
from app.models.user import ROLE_ADMIN
from app.database import set_write_intent
from app.services.business_time import business_date_for, parse_day_start

MAX_QUANTITY = Decimal("1000000")
MAX_RETRIES = 3

# Adjustment reasons accepted for stock entries and corrections.
ENTRY_REASONS = {"daily_count", "spoilage", "damage", "recount"}


def _begin_write_transaction(db: Session) -> None:
    """Mark the request's write transaction as write-intent (D15/D29).

    Authentication/CSRF dependencies already ran read queries, which started
    a DEFERRED read transaction. Close it (harmless — nothing was written)
    and flag the current thread so the engine begins the next transaction as
    BEGIN IMMEDIATE, serializing stock writers. Ordinary read-only requests
    never set the flag and never take a write lock. Callers MUST clear the
    flag (see the try/finally in each public function).
    """
    if db.get_bind().dialect.name == "sqlite":
        db.rollback()
        set_write_intent(True)


def _entry_matches(
    transaction: InventoryTransaction,
    *,
    ingredient_id: int,
    mode: str,
    quantity: Decimal,
    reason: str | None,
) -> bool:
    """Safeguard: a replayed idempotency key must carry the SAME payload."""
    if transaction.mode != mode or transaction.reason != reason:
        return False
    if transaction.ingredient_id != ingredient_id:
        return False
    if mode == MODE_USED:
        return transaction.quantity_delta == -quantity
    return transaction.resulting_quantity == quantity  # remaining = absolute count


def _corrected_quantity_of(
    correction: InventoryTransaction, original: InventoryTransaction
) -> Decimal:
    """Re-derive the corrected quantity a correction row represents."""
    new_delta = original.quantity_delta + correction.quantity_delta
    if original.mode == MODE_USED:
        return -new_delta
    balance_before = original.resulting_quantity - original.quantity_delta
    return new_delta + balance_before


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _find_by_idempotency_key(db: Session, key: str) -> InventoryTransaction | None:
    return db.scalar(
        select(InventoryTransaction).where(
            InventoryTransaction.idempotency_key == key
        )
    )


def _business_date(settings: Settings, moment: datetime) -> date:
    return business_date_for(
        moment, settings.business_timezone,
        parse_day_start(settings.business_day_start_time),
    )


def _apply_balance_update(
    db: Session,
    *,
    ingredient_id: int,
    delta: Decimal,
    allow_negative: bool,
    now: datetime,
    counted_by: int | None,
) -> Decimal:
    """The atomic conditional UPDATE (D15). Returns the new balance.

    The negative-stock guard lives in the WHERE clause, so even a concurrent
    change between our read and this statement can only produce rowcount 0 —
    never a wrong balance.
    """
    values = {
        "current_quantity": IngredientStockState.current_quantity + delta,
        "last_transaction_at": now,
        "version": IngredientStockState.version + 1,
    }
    if counted_by is not None:  # reconciliation = physical verification (D3)
        values["last_counted_at"] = now
        values["last_counted_by"] = counted_by
        values["confidence"] = "high"

    stmt = (
        update(IngredientStockState)
        .where(IngredientStockState.ingredient_id == ingredient_id)
    )
    if not allow_negative:
        stmt = stmt.where(
            IngredientStockState.current_quantity + delta >= 0
        )
    result = db.execute(stmt.values(**values))
    if result.rowcount != 1:
        raise ApiError(
            409, "insufficient_stock",
            "The stock balance changed or would become negative. "
            "Refresh and try again.",
        )
    return db.get(IngredientStockState, ingredient_id).current_quantity


def _run_atomic(db: Session, work):
    """Run ``work()`` inside one transaction with bounded lock retries (D15).

    Always clears the thread's write-intent flag on exit (D29).
    """
    try:
        for attempt in range(MAX_RETRIES):
            try:
                result = work()
                db.commit()
                return result
            except OperationalError as exc:
                db.rollback()
                if "locked" in str(exc).lower() and attempt < MAX_RETRIES - 1:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise ApiError(
                    503, "database_busy",
                    "The database is busy. Please try again.",
                )
            except Exception:
                db.rollback()
                raise
        raise ApiError(503, "database_busy", "The database is busy. Please try again.")
    finally:
        set_write_intent(False)


def record_stock_entry(
    db: Session,
    *,
    ingredient_id: int,
    user: User,
    mode: str,
    quantity: Decimal,
    reason: str | None,
    idempotency_key: str,
    confirmed: bool,
    allow_negative: bool,
    settings: Settings,
) -> tuple[InventoryTransaction, bool]:
    """Record a stock entry. Returns ``(transaction, was_idempotent_replay)``."""
    if mode not in (MODE_REMAINING, MODE_USED):
        raise ApiError(400, "invalid_mode", "Mode must be 'remaining' or 'used'.")
    if mode == MODE_USED and quantity <= 0:
        raise ApiError(400, "invalid_quantity", "Used quantity must be greater than zero.")
    if mode == MODE_REMAINING and quantity < 0:
        raise ApiError(400, "invalid_quantity", "Remaining quantity cannot be negative.")
    if quantity > MAX_QUANTITY:
        raise ApiError(400, "invalid_quantity", "Quantity is unreasonably large.")
    if reason is not None and reason not in ENTRY_REASONS:
        raise ApiError(400, "invalid_reason", f"Reason must be one of: {sorted(ENTRY_REASONS)}.")

    _begin_write_transaction(db)

    # Replay check first: a retry returns the ORIGINAL result unchanged (D6) —
    # but ONLY when the payload is identical. The same key with a DIFFERENT
    # payload is a client bug and must be rejected loudly (D28).
    existing = _find_by_idempotency_key(db, idempotency_key)
    if existing is not None:
        set_write_intent(False)  # replay path exits before _run_atomic
        if not _entry_matches(
            existing, ingredient_id=ingredient_id, mode=mode,
            quantity=quantity, reason=reason,
        ):
            raise ApiError(
                409, "idempotency_conflict",
                "This idempotency key was already used with a different request.",
            )
        return existing, True

    effective_allow_negative = allow_negative and user.role == ROLE_ADMIN
    now = _now()

    def work() -> InventoryTransaction:
        ingredient = db.get(Ingredient, ingredient_id)
        if ingredient is None:
            raise ApiError(404, "ingredient_not_found", "Ingredient not found.")
        if ingredient.status != STATUS_ACTIVE:
            raise ApiError(400, "ingredient_inactive", "Ingredient is archived.")

        state = db.get(IngredientStockState, ingredient_id)
        current = state.current_quantity

        if mode == MODE_USED:
            delta = -quantity
            entry_type = TYPE_CONSUMPTION
        else:
            delta = quantity - current  # reconciliation, NOT consumption (D3)
            entry_type = TYPE_RECONCILIATION

        new_balance = current + delta

        if new_balance < 0 and not effective_allow_negative:
            raise ApiError(
                400, "negative_stock",
                "This entry would make stock negative. "
                "An administrator can override with allow_negative.",
            )
        if abs(delta) > settings.large_adjustment_threshold and not confirmed:
            raise ApiError(
                400, "confirmation_required",
                f"This adjustment of {delta} is unusually large. "
                "Re-submit with confirmed=true to apply it.",
            )

        applied_balance = _apply_balance_update(
            db,
            ingredient_id=ingredient_id,
            delta=delta,
            allow_negative=effective_allow_negative,
            now=now,
            counted_by=user.id if mode == MODE_REMAINING else None,
        )

        transaction = InventoryTransaction(
            ingredient_id=ingredient_id,
            type=entry_type,
            quantity_delta=delta,
            resulting_quantity=applied_balance,
            reason=reason,
            mode=mode,
            entered_by=user.id,
            business_date=_business_date(settings, now),
            created_at=now,
            idempotency_key=idempotency_key,
        )
        db.add(transaction)
        db.flush()
        return transaction

    try:
        return _run_atomic(db, work), False
    except IntegrityError:
        # Lost an idempotency race: another request inserted the same key.
        existing = _find_by_idempotency_key(db, idempotency_key)
        if existing is not None:
            if not _entry_matches(
                existing, ingredient_id=ingredient_id, mode=mode,
                quantity=quantity, reason=reason,
            ):
                raise ApiError(
                    409, "idempotency_conflict",
                    "This idempotency key was already used with a different request.",
                )
            return existing, True
        raise


def correct_transaction(
    db: Session,
    *,
    transaction_id: int,
    user: User,
    corrected_quantity: Decimal,
    reason: str,
    idempotency_key: str,
    confirmed: bool,
    settings: Settings,
) -> tuple[InventoryTransaction, bool]:
    """Correct a mistaken entry WITHOUT editing it (D2).

    Appends a compensating ``correction`` row whose delta moves the CURRENT
    balance by (what should have been recorded) − (what was recorded), so
    ledger consistency holds even if later transactions already happened.
    """
    if corrected_quantity < 0 or corrected_quantity > MAX_QUANTITY:
        raise ApiError(400, "invalid_quantity", "Corrected quantity is invalid.")
    if not reason:
        raise ApiError(400, "invalid_reason", "A correction reason is required.")

    _begin_write_transaction(db)

    existing = _find_by_idempotency_key(db, idempotency_key)
    if existing is not None:
        set_write_intent(False)  # replay path exits before _run_atomic
        original = db.get(InventoryTransaction, transaction_id)
        if (
            original is None
            or existing.supersedes_id != original.id
            or existing.reason != reason
            or _corrected_quantity_of(existing, original) != corrected_quantity
        ):
            raise ApiError(
                409, "idempotency_conflict",
                "This idempotency key was already used with a different request.",
            )
        return existing, True

    now = _now()

    def work() -> InventoryTransaction:
        original = db.get(InventoryTransaction, transaction_id)
        if original is None:
            raise ApiError(404, "transaction_not_found", "Transaction not found.")
        if original.type not in (TYPE_CONSUMPTION, TYPE_RECONCILIATION):
            raise ApiError(
                400, "not_correctable",
                "Only stock entries (consumption/reconciliation) can be corrected.",
            )
        if original.entered_by != user.id and user.role != ROLE_ADMIN:
            raise ApiError(
                403, "forbidden",
                "You can only correct your own entries; ask an administrator otherwise.",
            )
        already = db.scalar(
            select(InventoryTransaction).where(
                InventoryTransaction.supersedes_id == original.id
            )
        )
        if already is not None:
            raise ApiError(
                409, "already_corrected",
                f"This entry was already corrected by transaction #{already.id}.",
            )
        # D27: a newer physical count has already verified reality. Adjusting
        # for an older mistake would silently corrupt that verified balance,
        # so the correction must be refused instead.
        newer_reconciliation = db.scalar(
            select(InventoryTransaction)
            .where(
                InventoryTransaction.ingredient_id == original.ingredient_id,
                InventoryTransaction.id > original.id,
                InventoryTransaction.type == TYPE_RECONCILIATION,
            )
            .limit(1)
        )
        if newer_reconciliation is not None:
            raise ApiError(
                409, "correction_after_reconciliation",
                f"A newer physical count (transaction "
                f"#{newer_reconciliation.id}) already verified this stock. "
                "Correct the count itself instead of the older entry.",
            )

        old_delta = original.quantity_delta
        balance_before_original = original.resulting_quantity - old_delta
        if original.mode == MODE_USED:
            new_delta = -corrected_quantity
        else:  # remaining: absolute count relative to the balance back then
            new_delta = corrected_quantity - balance_before_original
        correction_delta = new_delta - old_delta

        state = db.get(IngredientStockState, original.ingredient_id)
        new_balance = state.current_quantity + correction_delta
        if new_balance < 0:
            raise ApiError(
                400, "negative_stock",
                "This correction would make the current stock negative.",
            )
        if abs(correction_delta) > settings.large_adjustment_threshold and not confirmed:
            raise ApiError(
                400, "confirmation_required",
                f"This correction of {correction_delta} is unusually large. "
                "Re-submit with confirmed=true to apply it.",
            )

        applied_balance = _apply_balance_update(
            db,
            ingredient_id=original.ingredient_id,
            delta=correction_delta,
            allow_negative=False,
            now=now,
            counted_by=None,  # a correction is not a physical verification
        )

        correction = InventoryTransaction(
            ingredient_id=original.ingredient_id,
            type=TYPE_CORRECTION,
            quantity_delta=correction_delta,
            resulting_quantity=applied_balance,
            reason=reason,
            mode=None,
            entered_by=user.id,
            business_date=_business_date(settings, now),
            created_at=now,
            idempotency_key=idempotency_key,
            supersedes_id=original.id,
        )
        db.add(correction)
        db.flush()
        return correction

    try:
        return _run_atomic(db, work), False
    except IntegrityError:
        existing = _find_by_idempotency_key(db, idempotency_key)
        if existing is not None:
            original = db.get(InventoryTransaction, transaction_id)
            if (
                original is None
                or existing.supersedes_id != original.id
                or existing.reason != reason
                or _corrected_quantity_of(existing, original) != corrected_quantity
            ):
                raise ApiError(
                    409, "idempotency_conflict",
                    "This idempotency key was already used with a different request.",
                )
            return existing, True
        raise


def verify_ledger(db: Session) -> list[dict]:
    """Compare SUM(ledger deltas) with the cached balance per ingredient (D14).

    Returns one report row per ingredient; ``consistent`` is False when the
    cache and the ledger disagree (which should never happen — it indicates
    manual DB edits or a restored backup).
    """
    ledger_sums = dict(
        db.execute(
            select(
                InventoryTransaction.ingredient_id,
                func.coalesce(func.sum(InventoryTransaction.quantity_delta), 0),
            ).group_by(InventoryTransaction.ingredient_id)
        ).all()
    )
    states = db.scalars(select(IngredientStockState)).all()
    report = []
    for state in states:
        ledger_total = Decimal(ledger_sums.get(state.ingredient_id, 0))
        report.append({
            "ingredient_id": state.ingredient_id,
            "cached_balance": state.current_quantity,
            "ledger_balance": ledger_total,
            "consistent": ledger_total == state.current_quantity,
        })
    return report


def repair_ledger(db: Session, *, admin: User) -> dict:
    """Admin-only repair: rewrite drifted cached balances FROM the ledger.

    Never touches the ledger itself. Every repaired row is flagged
    (unresolved_discrepancy, confidence → low) and audit-logged.
    """
    _begin_write_transaction(db)
    try:
        report = verify_ledger(db)
        drifted = [row for row in report if not row["consistent"]]
        now = _now()

        def work() -> list[int]:
            repaired = []
            for row in drifted:
                state = db.get(IngredientStockState, row["ingredient_id"])
                before = str(state.current_quantity)
                state.current_quantity = row["ledger_balance"]
                state.unresolved_discrepancy = True
                state.confidence = "low"
                state.version += 1
                db.add(AuditLog(
                    user_id=admin.id,
                    action="ledger.repair",
                    entity_type="ingredient_stock_state",
                    entity_id=state.ingredient_id,
                    before_json=f'{{"current_quantity": "{before}"}}',
                    after_json=f'{{"current_quantity": "{row["ledger_balance"]}"}}',
                    created_at=now,
                ))
                repaired.append(state.ingredient_id)
            return repaired

        repaired_ids = _run_atomic(db, work) if drifted else []
        return {
            "checked": len(report),
            "drifted": len(drifted),
            "repaired_ingredient_ids": repaired_ids,
            "repaired_at": now,
        }
    finally:
        set_write_intent(False)
