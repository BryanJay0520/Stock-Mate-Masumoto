"""Delivery receipts (Phase 2 Step 3, decision D37).

Receipts are immutable; reversals are new receipts with negative item
quantities (reversal_of_id). Every receipt line links one-to-one to the
delivery_receipt ledger transaction it created.

Revision ID: 0004_delivery_receipts
Revises: 0003_purchase_orders
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_delivery_receipts"
down_revision = "0003_purchase_orders"
branch_labels = None
depends_on = None

QUANTITY = sa.Numeric(14, 3)


def upgrade() -> None:
    op.create_table(
        "delivery_receipts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "purchase_order_id", sa.Integer,
            sa.ForeignKey("purchase_orders.id"), nullable=False,
        ),
        sa.Column(
            "received_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_date", sa.Date, nullable=False),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "reversal_of_id", sa.Integer,
            sa.ForeignKey("delivery_receipts.id"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_delivery_receipts_order", "delivery_receipts", ["purchase_order_id"]
    )
    op.create_index(
        "ix_delivery_receipts_receipt_date", "delivery_receipts", ["receipt_date"]
    )

    op.create_table(
        "delivery_receipt_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "receipt_id", sa.Integer,
            sa.ForeignKey("delivery_receipts.id"), nullable=False,
        ),
        sa.Column(
            "purchase_order_item_id", sa.Integer,
            sa.ForeignKey("purchase_order_items.id"), nullable=False,
        ),
        sa.Column(
            "ingredient_id", sa.Integer,
            sa.ForeignKey("ingredients.id"), nullable=False,
        ),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column(
            "inventory_transaction_id", sa.Integer,
            sa.ForeignKey("inventory_transactions.id"), nullable=True, unique=True,
        ),
        sa.CheckConstraint("quantity <> 0", name="ck_receipt_items_nonzero"),
    )
    op.create_index(
        "ix_delivery_receipt_items_receipt_id",
        "delivery_receipt_items", ["receipt_id"],
    )
    op.create_index(
        "ix_receipt_items_order_item",
        "delivery_receipt_items", ["purchase_order_item_id"],
    )


def downgrade() -> None:
    op.drop_table("delivery_receipt_items")
    op.drop_table("delivery_receipts")
