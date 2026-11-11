"""Counting packages remain separate from supplier purchasing packages.

Revision ID: 0007_inventory_count_packages
Revises: 0006_receipt_staff_name
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_inventory_count_packages"
down_revision = "0006_receipt_staff_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingredients", sa.Column("count_package_enabled", sa.Boolean(),
                                          nullable=False, server_default=sa.false()))
    op.add_column("ingredients", sa.Column("count_package_name", sa.String(40), nullable=True))
    op.add_column("ingredients", sa.Column("count_units_per_package", sa.Numeric(14, 3), nullable=True))
    op.add_column("ingredients", sa.Column("count_package_version", sa.Integer(),
                                          nullable=False, server_default="0"))
    op.add_column("inventory_transactions", sa.Column("counting_json", sa.Text(), nullable=True))
    op.add_column("supplier_ingredients", sa.Column("purchase_package_name", sa.String(40), nullable=True))
    op.add_column("supplier_ingredients", sa.Column("whole_packages_only", sa.Boolean(),
                                                   nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("supplier_ingredients") as batch:
        batch.drop_column("whole_packages_only")
        batch.drop_column("purchase_package_name")
    with op.batch_alter_table("inventory_transactions") as batch:
        batch.drop_column("counting_json")
    with op.batch_alter_table("ingredients") as batch:
        batch.drop_column("count_package_version")
        batch.drop_column("count_units_per_package")
        batch.drop_column("count_package_name")
        batch.drop_column("count_package_enabled")
