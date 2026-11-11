"""Purchase orders and line items (Phase 2 Step 2, decision D36).

Orders keep their own supplier_id forever (historical integrity). One line
per ingredient per order prevents double-counted pending quantities.
quantity_received exists from the start so Step 3 delivery receipts only
add processing logic, not schema.

Revision ID: 0003_purchase_orders
Revises: 0002_supplier_scheduling
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_purchase_orders"
down_revision = "0002_supplier_scheduling"
branch_labels = None
depends_on = None

QUANTITY = sa.Numeric(14, 3)

_STATUSES = (
    "draft", "ordered", "confirmed", "partially_delivered",
    "delivered", "delayed", "cancelled",
)


def upgrade() -> None:
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), nullable=False
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expected_delivery_date", sa.Date, nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ordered_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in _STATUSES) + ")",
            name="ck_orders_status",
        ),
    )
    op.create_index(
        "ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"]
    )
    op.create_index(
        "ix_orders_supplier_status", "purchase_orders", ["supplier_id", "status"]
    )
    op.create_index(
        "ix_orders_expected_date", "purchase_orders", ["expected_delivery_date"]
    )

    op.create_table(
        "purchase_order_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "order_id", sa.Integer, sa.ForeignKey("purchase_orders.id"), nullable=False
        ),
        sa.Column(
            "ingredient_id", sa.Integer, sa.ForeignKey("ingredients.id"), nullable=False
        ),
        sa.Column("quantity_ordered", QUANTITY, nullable=False),
        sa.Column("quantity_received", QUANTITY, nullable=False),
        sa.UniqueConstraint("order_id", "ingredient_id", name="uq_order_items_ingredient"),
        sa.CheckConstraint("quantity_ordered > 0", name="ck_order_items_ordered_positive"),
        sa.CheckConstraint("quantity_received >= 0", name="ck_order_items_received_nonneg"),
        sa.CheckConstraint(
            "quantity_received <= quantity_ordered",
            name="ck_order_items_received_within_ordered",
        ),
    )
    op.create_index(
        "ix_purchase_order_items_order_id", "purchase_order_items", ["order_id"]
    )


def downgrade() -> None:
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
