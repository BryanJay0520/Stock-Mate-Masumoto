"""Purchase order service (Phase 2 Step 2, decision D36).

Responsibilities:
- Order CRUD with a strict state machine (draft → ordered → confirmed,
  ± delayed, → cancelled; delivery statuses arrive with Step 3 receipts).
- Server-side incoming-stock computation per ingredient:
  pending = quantity_ordered − quantity_received, summed per ingredient
  across qualifying orders. Drafts and cancelled orders never count;
  ordered/delayed are "unconfirmed" (uncertain), confirmed/partially
  delivered are "confirmed" (reliable) — the two are never merged.
- The archive guard for suppliers lives here too: a supplier with OPEN
  orders cannot be archived (architecture §5.2).

Delivery receipt processing is deliberately NOT implemented here (Step 3);
``quantity_received`` only changes through that future flow.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import (
    Ingredient,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
    SupplierIngredient,
    User,
)
from app.models.ordering import (
    INCOMING_CONFIRMED_STATUSES,
    INCOMING_UNCONFIRMED_STATUSES,
    ORDER_CANCELLED,
    ORDER_CONFIRMED,
    ORDER_DELAYED,
    ORDER_DRAFT,
    ORDER_ORDERED,
    ORDER_PARTIALLY_DELIVERED,
)
from app.models.supplier import STATUS_ACTIVE
from app.services.catalog import _audit
from app.services.inventory import _begin_write_transaction, _run_atomic

ZERO = Decimal("0.000")  # keep the 3-decimal scale in serialized responses


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _order_snapshot(order: PurchaseOrder) -> dict:
    return {
        "supplier_id": order.supplier_id,
        "status": order.status,
        "expected_delivery_date": order.expected_delivery_date.isoformat(),
        "notes": order.notes,
        "ordered_by": order.ordered_by,
        "ordered_at": order.ordered_at.isoformat() if order.ordered_at else None,
        "items": [
            {
                "ingredient_id": item.ingredient_id,
                "quantity_ordered": str(item.quantity_ordered),
                "quantity_received": str(item.quantity_received),
            }
            for item in order.items
        ],
    }


def _get_order(db: Session, order_id: int) -> PurchaseOrder:
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise ApiError(404, "order_not_found", "Purchase order not found.")
    return order


def _validate_items(db: Session, *, supplier: Supplier, items: list) -> None:
    """Every item must reference an active ingredient the supplier provides,
    with no duplicate ingredient lines (also DB-enforced)."""
    seen: set[int] = set()
    for item in items:
        if item.ingredient_id in seen:
            raise ApiError(
                400, "duplicate_item",
                f"Ingredient {item.ingredient_id} appears twice in this order.",
            )
        seen.add(item.ingredient_id)
        ingredient = db.get(Ingredient, item.ingredient_id)
        if ingredient is None or ingredient.status != STATUS_ACTIVE:
            raise ApiError(
                400, "ingredient_not_found",
                f"Ingredient {item.ingredient_id} does not exist or is archived.",
            )
        link = db.scalar(
            select(SupplierIngredient).where(
                SupplierIngredient.supplier_id == supplier.id,
                SupplierIngredient.ingredient_id == item.ingredient_id,
            )
        )
        if link is None:
            raise ApiError(
                400, "ingredient_not_supplied",
                f"'{ingredient.name}' is not supplied by '{supplier.name}'.",
            )


def _get_active_supplier(db: Session, supplier_id: int) -> Supplier:
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise ApiError(404, "supplier_not_found", "Supplier not found.")
    if supplier.status != STATUS_ACTIVE:
        raise ApiError(
            400, "supplier_inactive",
            "Orders cannot be created for an archived supplier.",
        )
    return supplier


# --- CRUD ---------------------------------------------------------------------

def create_order(db: Session, *, user: User, data) -> PurchaseOrder:
    _begin_write_transaction(db)

    def work() -> PurchaseOrder:
        supplier = _get_active_supplier(db, data.supplier_id)
        _validate_items(db, supplier=supplier, items=data.items)
        order = PurchaseOrder(
            supplier_id=supplier.id,
            status=ORDER_DRAFT,
            expected_delivery_date=data.expected_delivery_date,
            created_by=user.id,
            notes=data.notes,
        )
        order.items = [
            PurchaseOrderItem(
                ingredient_id=item.ingredient_id,
                quantity_ordered=item.quantity_ordered,
                quantity_received=ZERO,
            )
            for item in data.items
        ]
        db.add(order)
        db.flush()
        _audit(db, user, "order.create", "purchase_order", order.id, None,
               _order_snapshot(order))
        return order

    order = _run_atomic(db, work)
    db.refresh(order)  # items collection written inside the transaction (D31)
    return order


def update_order(db: Session, *, user: User, order_id: int, data) -> PurchaseOrder:
    _begin_write_transaction(db)

    def work() -> PurchaseOrder:
        order = _get_order(db, order_id)
        if order.status != ORDER_DRAFT:
            raise ApiError(
                409, "order_not_draft",
                "Only draft orders can be edited. Cancel and recreate instead.",
            )
        before = _order_snapshot(order)
        if data.expected_delivery_date is not None:
            order.expected_delivery_date = data.expected_delivery_date
        if data.notes is not None or "notes" in data.model_fields_set:
            order.notes = data.notes
        if data.items is not None:
            supplier = _get_supplier_for_order(db, order)
            _validate_items(db, supplier=supplier, items=data.items)
            order.items = [
                PurchaseOrderItem(
                    ingredient_id=item.ingredient_id,
                    quantity_ordered=item.quantity_ordered,
                    quantity_received=ZERO,
                )
                for item in data.items
            ]
            db.flush()
        _audit(db, user, "order.update", "purchase_order", order.id, before,
               _order_snapshot(order))
        return order

    order = _run_atomic(db, work)
    db.refresh(order)
    return order


def _get_supplier_for_order(db: Session, order: PurchaseOrder) -> Supplier:
    supplier = db.get(Supplier, order.supplier_id)
    if supplier is None:  # FK makes this impossible; guard for clarity
        raise ApiError(404, "supplier_not_found", "Supplier not found.")
    return supplier


# --- Status transitions ---------------------------------------------------------

def _transition(db: Session, *, user: User, order_id: int,
                allowed_from: tuple[str, ...], to: str, action: str,
                on_transition=None) -> PurchaseOrder:
    """Idempotent state machine helper: re-requesting the current state is a
    no-op; every other source state is a 409."""
    _begin_write_transaction(db)

    def work() -> PurchaseOrder:
        order = _get_order(db, order_id)
        if order.status == to:
            return order  # idempotent: safe to double-click / retry
        if order.status not in allowed_from:
            raise ApiError(
                409, "invalid_status_transition",
                f"Cannot move an order from '{order.status}' to '{to}'.",
            )
        before = _order_snapshot(order)
        order.status = to
        if on_transition is not None:
            on_transition(order)
        _audit(db, user, action, "purchase_order", order.id, before,
               _order_snapshot(order))
        return order

    order = _run_atomic(db, work)
    db.refresh(order)
    return order


def mark_ordered(db: Session, *, user: User, order_id: int) -> PurchaseOrder:
    """Draft → ordered. Records who placed it and when; from now on the
    quantities count as (unconfirmed) incoming stock."""
    return _transition(
        db, user=user, order_id=order_id,
        allowed_from=(ORDER_DRAFT,), to=ORDER_ORDERED, action="order.mark_ordered",
        on_transition=lambda order: (
            setattr(order, "ordered_by", user.id),
            setattr(order, "ordered_at", _now()),
        ),
    )


def confirm_order(db: Session, *, user: User, order_id: int) -> PurchaseOrder:
    """Ordered/delayed → confirmed: the supplier confirmed the shipment, so
    the quantities become reliable incoming supply."""
    return _transition(
        db, user=user, order_id=order_id,
        allowed_from=(ORDER_ORDERED, ORDER_DELAYED),
        to=ORDER_CONFIRMED, action="order.confirm",
    )


def delay_order(db: Session, *, user: User, order_id: int) -> PurchaseOrder:
    """Ordered/confirmed → delayed: still incoming, but flagged late."""
    return _transition(
        db, user=user, order_id=order_id,
        allowed_from=(ORDER_ORDERED, ORDER_CONFIRMED),
        to=ORDER_DELAYED, action="order.delay",
    )


def cancel_order(db: Session, *, user: User, order_id: int) -> PurchaseOrder:
    """Any pre-delivery state → cancelled: quantities stop counting as
    incoming immediately."""
    return _transition(
        db, user=user, order_id=order_id,
        allowed_from=(ORDER_DRAFT, ORDER_ORDERED, ORDER_CONFIRMED, ORDER_DELAYED),
        to=ORDER_CANCELLED, action="order.cancel",
    )


# --- Incoming stock computation (server-side, D36) --------------------------------

def incoming_by_ingredient(db: Session) -> dict[int, dict]:
    """Pending supply per ingredient, aggregated in SQL — one row per
    ingredient per bucket, so multiple orders can never double-count."""
    pending = PurchaseOrderItem.quantity_ordered - PurchaseOrderItem.quantity_received
    rows = db.execute(
        select(
            PurchaseOrderItem.ingredient_id,
            PurchaseOrder.status,
            func.sum(pending),
            func.min(PurchaseOrder.expected_delivery_date),
        )
        .join(PurchaseOrder, PurchaseOrderItem.order_id == PurchaseOrder.id)
        .where(
            PurchaseOrder.status.in_(
                INCOMING_UNCONFIRMED_STATUSES + INCOMING_CONFIRMED_STATUSES
            ),
            pending > 0,
        )
        .group_by(PurchaseOrderItem.ingredient_id, PurchaseOrder.status)
    ).all()

    summary: dict[int, dict] = {}
    for ingredient_id, status, total, earliest in rows:
        bucket = summary.setdefault(ingredient_id, {
            "incoming_quantity": ZERO,
            "incoming_ordered_quantity": ZERO,
            "incoming_confirmed_quantity": ZERO,
            "expected_delivery": None,
            "incoming_status": None,
            "_has_delayed": False,
            "_has_confirmed": False,
        })
        bucket["incoming_quantity"] += total
        if status in INCOMING_CONFIRMED_STATUSES:
            bucket["incoming_confirmed_quantity"] += total
            bucket["_has_confirmed"] = True
        else:
            bucket["incoming_ordered_quantity"] += total
            if status == ORDER_DELAYED:
                bucket["_has_delayed"] = True
        if bucket["expected_delivery"] is None or earliest < bucket["expected_delivery"]:
            bucket["expected_delivery"] = earliest

    for bucket in summary.values():
        # Worst-case first: a delayed shipment outranks confirmed, which
        # outranks merely ordered.
        if bucket["_has_delayed"]:
            bucket["incoming_status"] = ORDER_DELAYED
        elif bucket["_has_confirmed"]:
            bucket["incoming_status"] = ORDER_CONFIRMED
        else:
            bucket["incoming_status"] = ORDER_ORDERED
        del bucket["_has_delayed"], bucket["_has_confirmed"]
    return summary


def projected_quantity(current: Decimal, bucket: dict | None) -> Decimal:
    """Available + ALL incoming (ordered + confirmed). Always labeled as a
    projection in responses and UI — never as physical stock."""
    incoming = bucket["incoming_quantity"] if bucket else ZERO
    return current + incoming


def supplier_has_open_orders(db: Session, supplier_id: int) -> list[PurchaseOrder]:
    from app.models.ordering import OPEN_ORDER_STATUSES
    return db.scalars(
        select(PurchaseOrder).where(
            PurchaseOrder.supplier_id == supplier_id,
            PurchaseOrder.status.in_(OPEN_ORDER_STATUSES),
        )
    ).all()
