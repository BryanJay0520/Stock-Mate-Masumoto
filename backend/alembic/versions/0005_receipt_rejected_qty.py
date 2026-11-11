"""Rejected/damaged quantities on receipt lines (Phase 2 Step 3, D37).

Adds ``quantity_rejected`` to receipt items: damaged goods are recorded for
audit and supplier follow-up, but never enter stock and never reduce the
outstanding supplier obligation. The nonzero check is widened so a
fully-rejected line (accepted 0, rejected > 0) is representable.

0004 stays untouched — this migration is additive for databases that
already applied it.

Revision ID: 0005_receipt_rejected_qty
Revises: 0004_delivery_receipts
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_receipt_rejected_qty"
down_revision = "0004_delivery_receipts"
branch_labels = None
depends_on = None

QUANTITY = sa.Numeric(14, 3)


def upgrade() -> None:
    with op.batch_alter_table("delivery_receipt_items", recreate="always") as batch:
        batch.add_column(
            sa.Column("quantity_rejected", QUANTITY, nullable=False, server_default="0")
        )
        batch.drop_constraint("ck_receipt_items_nonzero", type_="check")
        batch.create_check_constraint(
            "ck_receipt_items_nonzero", "quantity <> 0 OR quantity_rejected <> 0"
        )


def downgrade() -> None:
    with op.batch_alter_table("delivery_receipt_items", recreate="always") as batch:
        batch.drop_constraint("ck_receipt_items_nonzero", type_="check")
        batch.create_check_constraint("ck_receipt_items_nonzero", "quantity <> 0")
        batch.drop_column("quantity_rejected")
