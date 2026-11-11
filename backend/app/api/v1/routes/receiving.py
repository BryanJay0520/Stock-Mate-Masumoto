"""Delivery receiving API (Phase 2 Step 3).

Staff confirm what physically arrived; the server posts the stock into the
ledger atomically. Reversals are admin-only. All mutations are CSRF-protected
and idempotent via client-supplied keys.
"""

from fastapi import APIRouter, Depends, Request
from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import csrf_protect, get_current_user, require_admin
from app.database import get_db
from app.models import DeliveryReceipt, DeliveryReceiptItem, PurchaseOrderItem, User
from app.schemas.receiving import (
    DeliveryReceiptOut,
    ReceiptItemOut,
    ReceiveDeliveryIn,
    ReverseReceiptIn,
)
from app.services import receiving

router = APIRouter()


def _receipt_out(db: Session, receipt: DeliveryReceipt) -> DeliveryReceiptOut:
    reversed_by = db.scalar(
        select(DeliveryReceipt.id).where(
            DeliveryReceipt.reversal_of_id == receipt.id
        )
    )
    return DeliveryReceiptOut(
        id=receipt.id,
        purchase_order_id=receipt.purchase_order_id,
        received_by=receipt.received_by,
        received_by_name=receipt.receiver.username if receipt.receiver else None,
        received_by_staff_name=receipt.received_by_staff_name,
        received_at=receipt.received_at.replace(tzinfo=timezone.utc)
        if receipt.received_at.tzinfo is None else receipt.received_at,
        receipt_date=receipt.receipt_date,
        notes=receipt.notes,
        reversal_of_id=receipt.reversal_of_id,
        is_reversal=receipt.reversal_of_id is not None,
        reversed_by_id=reversed_by,
        items=[
            ReceiptItemOut(
                id=item.id,
                ingredient_id=item.ingredient_id,
                ingredient_name=item.ingredient.name if item.ingredient else None,
                quantity=item.quantity,
                quantity_rejected=item.quantity_rejected,
                outstanding_quantity=db.get(PurchaseOrderItem, item.purchase_order_item_id).quantity_ordered
                - db.scalar(select(func.coalesce(func.sum(DeliveryReceiptItem.quantity), 0)).where(
                    DeliveryReceiptItem.purchase_order_item_id == item.purchase_order_item_id,
                    DeliveryReceiptItem.receipt_id <= receipt.id,
                )),
                inventory_transaction_id=item.inventory_transaction_id,
            )
            for item in receipt.items
        ],
    )


@router.post(
    "/purchase-orders/{order_id}/receive",
    status_code=201, response_model=DeliveryReceiptOut,
    dependencies=[Depends(csrf_protect)],
)
def receive_delivery(
    order_id: int,
    payload: ReceiveDeliveryIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    receipt, _replay = receiving.receive_delivery(
        db, user=user, order_id=order_id, lines=payload.lines,
        receipt_date=payload.receipt_date, notes=payload.notes,
        idempotency_key=payload.idempotency_key,
        received_by_staff_name=payload.received_by_staff_name,
        settings=request.app.state.settings,
    )
    return _receipt_out(db, receipt)


@router.get(
    "/purchase-orders/{order_id}/receipts",
    response_model=list[DeliveryReceiptOut],
)
def list_receipts(
    order_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return [
        _receipt_out(db, receipt)
        for receipt in receiving.list_receipts(db, order_id=order_id)
    ]


@router.post(
    "/receipts/{receipt_id}/reverse",
    status_code=201, response_model=DeliveryReceiptOut,
    dependencies=[Depends(csrf_protect)],
)
def reverse_receipt(
    receipt_id: int,
    payload: ReverseReceiptIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    reversal, _replay = receiving.reverse_receipt(
        db, admin=admin, receipt_id=receipt_id, notes=payload.notes,
        idempotency_key=payload.idempotency_key,
        received_by_staff_name=payload.received_by_staff_name,
        settings=request.app.state.settings,
    )
    return _receipt_out(db, reversal)
