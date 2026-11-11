"""Staff receiver name on delivery receipts (Phase 2 Step 3 review).

Restaurants may share one StockMate account, so every receipt must also
record the name of the employee who physically accepted the delivery
(``received_by_staff_name``). The authenticated account id (``received_by``)
remains for internal audit. Additive: existing rows get an empty default.

Revision ID: 0006_receipt_staff_name
Revises: 0005_receipt_rejected_qty
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_receipt_staff_name"
down_revision = "0005_receipt_rejected_qty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("delivery_receipts", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "received_by_staff_name", sa.String(120),
                nullable=False, server_default="",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("delivery_receipts", recreate="always") as batch:
        batch.drop_column("received_by_staff_name")
