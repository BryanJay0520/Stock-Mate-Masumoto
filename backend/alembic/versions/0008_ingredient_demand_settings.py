"""Independent editable weekday demand inputs. Existing data is untouched.

Revision ID: 0008_ingredient_demand_settings
Revises: 0007_inventory_count_packages
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_ingredient_demand_settings"
down_revision = "0007_inventory_count_packages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    quantities = ("monday", "tuesday", "wednesday", "thursday", "friday",
                  "saturday", "sunday", "safety_stock")
    op.create_table(
        "ingredient_demand_settings",
        sa.Column("ingredient_id", sa.Integer(), sa.ForeignKey("ingredients.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *(sa.Column(day, sa.Numeric(14, 3), nullable=True) for day in quantities),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.CheckConstraint(f"{day} IS NULL OR {day} >= 0", name=f"ck_demand_{day}_nonneg")
          for day in quantities),
        sa.CheckConstraint("version > 0", name="ck_demand_version_positive"),
    )


def downgrade() -> None:
    op.drop_table("ingredient_demand_settings")
