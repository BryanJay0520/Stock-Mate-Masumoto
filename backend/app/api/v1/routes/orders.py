"""Purchase order API (Phase 2 Step 2).

Staff may create drafts, edit drafts, mark orders placed, confirm, and flag
delays (restaurant reality: whoever called the supplier records it).
Cancellation is admin-only. Delivery receipt processing is Step 3 and does
not exist yet.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import csrf_protect, get_current_user, require_admin
from app.database import get_db
from app.errors import ApiError
from app.models import PurchaseOrder, User
from app.schemas.ordering import (
    OrderItemOut,
    PurchaseOrderIn,
    PurchaseOrderOut,
    PurchaseOrderUpdate,
)
from app.services import ordering

router = APIRouter()


def _order_out(order: PurchaseOrder) -> PurchaseOrderOut:
    return PurchaseOrderOut(
        id=order.id,
        supplier_id=order.supplier_id,
        supplier_name=order.supplier.name if order.supplier else None,
        status=order.status,
        expected_delivery_date=order.expected_delivery_date,
        created_by=order.created_by,
        created_by_name=order.creator.username if order.creator else None,
        ordered_by=order.ordered_by,
        ordered_by_name=order.ordered_by_user.username if order.ordered_by_user else None,
        ordered_at=order.ordered_at,
        notes=order.notes,
        created_at=order.created_at,
        items=[
            OrderItemOut(
                id=item.id,
                ingredient_id=item.ingredient_id,
                ingredient_name=item.ingredient.name if item.ingredient else None,
                quantity_ordered=item.quantity_ordered,
                quantity_received=item.quantity_received,
                pending_quantity=item.quantity_ordered - item.quantity_received,
            )
            for item in order.items
        ],
    )


@router.get("/purchase-orders", response_model=list[PurchaseOrderOut])
def list_orders(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    status: str | None = Query(
        default=None,
        description="Comma-separated statuses, e.g. 'ordered,confirmed,delayed'",
    ),
    supplier_id: int | None = None,
    limit: int = Query(default=100, le=500),
):
    stmt = select(PurchaseOrder).order_by(
        PurchaseOrder.expected_delivery_date, PurchaseOrder.id
    ).limit(limit)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        stmt = stmt.where(PurchaseOrder.status.in_(statuses))
    if supplier_id is not None:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    return [_order_out(order) for order in db.scalars(stmt).all()]


@router.post(
    "/purchase-orders", status_code=201, response_model=PurchaseOrderOut,
    dependencies=[Depends(csrf_protect)],
)
def create_order(
    payload: PurchaseOrderIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _order_out(ordering.create_order(db, user=user, data=payload))


@router.get("/purchase-orders/{order_id}", response_model=PurchaseOrderOut)
def get_order(
    order_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise ApiError(404, "order_not_found", "Purchase order not found.")
    return _order_out(order)


@router.patch(
    "/purchase-orders/{order_id}", response_model=PurchaseOrderOut,
    dependencies=[Depends(csrf_protect)],
)
def update_order(
    order_id: int,
    payload: PurchaseOrderUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _order_out(ordering.update_order(db, user=user, order_id=order_id, data=payload))


@router.post(
    "/purchase-orders/{order_id}/mark-ordered", response_model=PurchaseOrderOut,
    dependencies=[Depends(csrf_protect)],
)
def mark_ordered(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _order_out(ordering.mark_ordered(db, user=user, order_id=order_id))


@router.post(
    "/purchase-orders/{order_id}/confirm", response_model=PurchaseOrderOut,
    dependencies=[Depends(csrf_protect)],
)
def confirm_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _order_out(ordering.confirm_order(db, user=user, order_id=order_id))


@router.post(
    "/purchase-orders/{order_id}/delay", response_model=PurchaseOrderOut,
    dependencies=[Depends(csrf_protect)],
)
def delay_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _order_out(ordering.delay_order(db, user=user, order_id=order_id))


@router.post(
    "/purchase-orders/{order_id}/cancel", response_model=PurchaseOrderOut,
    dependencies=[Depends(csrf_protect)],
)
def cancel_order(
    order_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _order_out(ordering.cancel_order(db, user=admin, order_id=order_id))
