"""Delivery receiving service (Phase 2 Step 3, decisions D2, D6, D36, D37).

A receipt is the only path that increases physical stock from a purchase
order. One atomic transaction (BEGIN IMMEDIATE on SQLite, D15) writes, for
every received line: the immutable ``delivery_receipt`` ledger row, the
cached balance update, the order item's ``quantity_received`` increment, and
the receipt rows themselves — all linked together. Partial deliveries keep
the outstanding remainder visible; receipts are immutable and mistakes are
undone by reversal receipts, never by editing.

Receiving never refreshes count confidence: ``counted_by=None`` on the
balance update, so ``last_counted_at``/``confidence`` stay untouched (D3).
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import set_write_intent
from app.errors import ApiError
from app.models import (
    DeliveryReceipt,
    DeliveryReceiptItem,
    InventoryTransaction,
    PurchaseOrder,
    PurchaseOrderItem,
    User,
)
from app.models.inventory import TYPE_DELIVERY_RECEIPT
from app.models.ordering import (
    ORDER_CONFIRMED,
    ORDER_DELAYED,
    ORDER_DELIVERED,
    ORDER_ORDERED,
    ORDER_PARTIALLY_DELIVERED,
)
from app.services.catalog import _audit
from app.services.inventory import (
    _apply_balance_update,
    _begin_write_transaction,
    _business_date,
    _run_atomic,
)

# Only these statuses may receive goods. Draft (never placed) and cancelled
# orders are rejected; delivered orders have nothing outstanding.
RECEIVABLE_STATUSES = (
    ORDER_ORDERED,
    ORDER_CONFIRMED,
    ORDER_DELAYED,
    ORDER_PARTIALLY_DELIVERED,
)

REASON_DELIVERY = "delivery"
REASON_REVERSAL = "receipt_reversal"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_order(db: Session, order_id: int) -> PurchaseOrder:
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise ApiError(404, "order_not_found", "Purchase order not found.")
    return order


def _receipt_snapshot(receipt: DeliveryReceipt) -> dict:
    return {
        "purchase_order_id": receipt.purchase_order_id,
        "received_by_staff_name": receipt.received_by_staff_name,
        "receipt_date": receipt.receipt_date.isoformat(),
        "notes": receipt.notes,
        "reversal_of_id": receipt.reversal_of_id,
        "items": [
            {
                "ingredient_id": item.ingredient_id,
                "quantity": str(item.quantity),
                "quantity_rejected": str(item.quantity_rejected),
            }
            for item in receipt.items
        ],
    }


def _receipt_matches(
    receipt: DeliveryReceipt, *, order_id: int, lines: list, receipt_date: date | None,
    notes: str | None, received_by_staff_name: str,
) -> bool:
    """Idempotency safeguard (D28): a replayed key must carry the SAME payload."""
    if receipt.purchase_order_id != order_id or receipt.reversal_of_id is not None:
        return False
    if receipt_date is not None and receipt.receipt_date != receipt_date:
        return False
    if receipt.notes != notes or receipt.received_by_staff_name != received_by_staff_name:
        return False
    expected = sorted(
        (line.ingredient_id, line.quantity, line.quantity_rejected) for line in lines
    )
    actual = sorted(
        (item.ingredient_id, item.quantity, item.quantity_rejected)
        for item in receipt.items
    )
    return expected == actual


def _recompute_order_status(order: PurchaseOrder) -> None:
    """Delivery statuses derive ONLY from received quantities (D37).

    Receiving is the sole source of ``partially_delivered``/``delivered``.
    A reversal that removes all received goods returns the order to
    ``ordered`` — always factual, since receiving requires a placed order.
    """
    items = order.items
    if all(item.quantity_received >= item.quantity_ordered for item in items):
        order.status = ORDER_DELIVERED
    elif any(item.quantity_received > 0 for item in items):
        order.status = ORDER_PARTIALLY_DELIVERED
    else:
        order.status = ORDER_ORDERED


def _post_line(
    db: Session, *, receipt: DeliveryReceipt, order_item: PurchaseOrderItem,
    quantity: Decimal, quantity_rejected: Decimal, user: User, now: datetime,
    business_date: date, reason: str, supersedes_id: int | None,
) -> None:
    """One received line: ledger row + cached balance + order item + link.

    The ledger row and balance update happen only for the ACCEPTED quantity;
    rejected goods are recorded on the receipt line for audit/supplier
    follow-up but never enter stock and never reduce the outstanding amount.
    """
    transaction_id = None
    if quantity != 0:
        new_balance = _apply_balance_update(
            db,
            ingredient_id=order_item.ingredient_id,
            delta=quantity,
            allow_negative=False,  # reversals cannot drive stock below zero
            now=now,
            counted_by=None,  # receiving is NOT a physical count (D3)
        )
        transaction = InventoryTransaction(
            ingredient_id=order_item.ingredient_id,
            type=TYPE_DELIVERY_RECEIPT,
            quantity_delta=quantity,
            resulting_quantity=new_balance,
            reason=reason,
            mode=None,
            entered_by=user.id,
            business_date=business_date,
            created_at=now,
            idempotency_key=None,  # receipt-level key guards the whole receipt
            supersedes_id=supersedes_id,
        )
        db.add(transaction)
        db.flush()
        transaction_id = transaction.id
        order_item.quantity_received += quantity  # negative on reversals
    receipt.items.append(DeliveryReceiptItem(
        purchase_order_item_id=order_item.id,
        ingredient_id=order_item.ingredient_id,
        quantity=quantity,
        quantity_rejected=quantity_rejected,
        inventory_transaction_id=transaction_id,
    ))


def receive_delivery(
    db: Session, *, user: User, order_id: int, lines: list,
    receipt_date: date | None, notes: str | None, idempotency_key: str,
    received_by_staff_name: str, settings: Settings,
) -> tuple[DeliveryReceipt, bool]:
    """Confirm received quantities. Returns ``(receipt, was_replay)``."""
    if len({line.ingredient_id for line in lines}) != len(lines):
        raise ApiError(
            400, "duplicate_item",
            "Each ingredient may appear only once per receipt.",
        )

    _begin_write_transaction(db)

    existing = db.scalar(
        select(DeliveryReceipt).where(
            DeliveryReceipt.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        set_write_intent(False)  # replay path exits before _run_atomic
        if not _receipt_matches(
            existing, order_id=order_id, lines=lines, receipt_date=receipt_date,
            notes=notes, received_by_staff_name=received_by_staff_name,
        ):
            raise ApiError(
                409, "idempotency_conflict",
                "This idempotency key was already used with a different request.",
            )
        return existing, True

    now = _now()
    business_date = receipt_date or _business_date(settings, now)

    def work() -> DeliveryReceipt:
        order = _get_order(db, order_id)
        if order.status not in RECEIVABLE_STATUSES:
            raise ApiError(
                409, "not_receivable",
                f"An order with status '{order.status}' cannot receive deliveries. "
                "Only placed orders (ordered/confirmed/delayed/partially "
                "delivered) are receivable.",
            )
        item_by_ingredient = {item.ingredient_id: item for item in order.items}
        receipt = DeliveryReceipt(
            purchase_order_id=order.id,
            received_by=user.id,
            received_by_staff_name=received_by_staff_name,
            received_at=now,
            receipt_date=business_date,
            notes=notes,
            idempotency_key=idempotency_key,
            created_at=now,
        )
        db.add(receipt)
        db.flush()
        for line in lines:
            if line.quantity == 0 and line.quantity_rejected == 0:
                raise ApiError(
                    400, "invalid_quantity",
                    "Each line needs an accepted or a rejected quantity.",
                )
            order_item = item_by_ingredient.get(line.ingredient_id)
            if order_item is None:
                raise ApiError(
                    400, "item_not_in_order",
                    f"Ingredient {line.ingredient_id} is not part of order "
                    f"#{order.id}.",
                )
            outstanding = order_item.quantity_ordered - order_item.quantity_received
            if line.quantity + line.quantity_rejected > outstanding:
                raise ApiError(
                    409, "over_receive",
                    f"Only {outstanding} outstanding for "
                    f"'{order_item.ingredient.name}'; cannot receive "
                    f"{line.quantity} accepted and {line.quantity_rejected} rejected.",
                )
            _post_line(
                db, receipt=receipt, order_item=order_item,
                quantity=line.quantity, quantity_rejected=line.quantity_rejected,
                user=user, now=now,
                business_date=business_date, reason=REASON_DELIVERY,
                supersedes_id=None,
            )
        db.flush()
        _recompute_order_status(order)
        _audit(db, user, "receipt.create", "delivery_receipt", receipt.id,
               None, _receipt_snapshot(receipt))
        _audit(db, user, "order.receive", "purchase_order", order.id,
               None, {"status": order.status, "receipt_id": receipt.id})
        return receipt

    try:
        receipt = _run_atomic(db, work)
        db.refresh(receipt)  # items written inside the transaction (D31)
        return receipt, False
    except IntegrityError:
        # Lost an idempotency race: another request inserted the same key.
        existing = db.scalar(
            select(DeliveryReceipt).where(
                DeliveryReceipt.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if not _receipt_matches(
                existing, order_id=order_id, lines=lines, receipt_date=receipt_date,
                notes=notes, received_by_staff_name=received_by_staff_name,
            ):
                raise ApiError(
                    409, "idempotency_conflict",
                    "This idempotency key was already used with a different request.",
                )
            return existing, True
        raise


def list_receipts(db: Session, *, order_id: int) -> list[DeliveryReceipt]:
    _get_order(db, order_id)  # 404 for unknown orders
    return db.scalars(
        select(DeliveryReceipt)
        .where(DeliveryReceipt.purchase_order_id == order_id)
        .order_by(DeliveryReceipt.id)
    ).all()


def reverse_receipt(
    db: Session, *, admin: User, receipt_id: int, notes: str | None,
    idempotency_key: str, received_by_staff_name: str, settings: Settings,
) -> tuple[DeliveryReceipt, bool]:
    """Admin-only undo of a mistaken receipt (D37).

    Appends a reversal receipt with negative quantities; the original is
    never touched. Stock cannot go negative — if the goods were already
    consumed, the reversal is refused and an admin stock correction is the
    correct tool instead.
    """
    _begin_write_transaction(db)

    existing = db.scalar(
        select(DeliveryReceipt).where(
            DeliveryReceipt.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        set_write_intent(False)
        if (existing.reversal_of_id != receipt_id
                or existing.received_by_staff_name != received_by_staff_name
                or existing.notes != (notes or f"Reversal of receipt #{receipt_id}.")):
            raise ApiError(
                409, "idempotency_conflict",
                "This idempotency key was already used with a different request.",
            )
        return existing, True

    now = _now()
    business_date = _business_date(settings, now)

    def work() -> DeliveryReceipt:
        original = db.get(DeliveryReceipt, receipt_id)
        if original is None:
            raise ApiError(404, "receipt_not_found", "Delivery receipt not found.")
        if original.reversal_of_id is not None:
            raise ApiError(
                400, "cannot_reverse_reversal",
                "A reversal receipt cannot itself be reversed.",
            )
        already = db.scalar(
            select(DeliveryReceipt).where(
                DeliveryReceipt.reversal_of_id == original.id
            )
        )
        if already is not None:
            raise ApiError(
                409, "already_reversed",
                f"This receipt was already reversed by receipt #{already.id}.",
            )
        order = _get_order(db, original.purchase_order_id)
        reversal = DeliveryReceipt(
            purchase_order_id=original.purchase_order_id,
            received_by=admin.id,
            received_by_staff_name=received_by_staff_name,
            received_at=now,
            receipt_date=business_date,
            notes=notes or f"Reversal of receipt #{original.id}.",
            idempotency_key=idempotency_key,
            reversal_of_id=original.id,
            created_at=now,
        )
        db.add(reversal)
        db.flush()
        for item in original.items:
            order_item = db.get(PurchaseOrderItem, item.purchase_order_item_id)
            _post_line(
                db, receipt=reversal, order_item=order_item,
                quantity=-item.quantity,
                quantity_rejected=-item.quantity_rejected,
                user=admin, now=now,
                business_date=business_date, reason=REASON_REVERSAL,
                supersedes_id=item.inventory_transaction_id,
            )
        db.flush()
        _recompute_order_status(order)
        _audit(db, admin, "receipt.reverse", "delivery_receipt", original.id,
               _receipt_snapshot(original), _receipt_snapshot(reversal))
        return reversal

    try:
        reversal = _run_atomic(db, work)
        db.refresh(reversal)
        return reversal, False
    except IntegrityError:
        existing = db.scalar(
            select(DeliveryReceipt).where(
                DeliveryReceipt.idempotency_key == idempotency_key
            )
        )
        if (existing is not None and existing.reversal_of_id == receipt_id
                and existing.received_by_staff_name == received_by_staff_name
                and existing.notes == (notes or f"Reversal of receipt #{receipt_id}.")):
            return existing, True
        raise
