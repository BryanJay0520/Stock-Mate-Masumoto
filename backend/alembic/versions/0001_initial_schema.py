"""Initial schema: users, sessions, units, suppliers, ingredients,
supplier_ingredients, ingredient_stock_state, inventory_transactions,
audit_logs, app_settings.

The ingredients ↔ supplier_ingredients relationship is circular
(ingredients.primary_supplier_id must reference a junction row, D24).
Both databases handle this safely the same way: create all tables first,
then add the composite foreign key afterwards (batch mode recreates the
table on SQLite; a plain ALTER on PostgreSQL).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-09
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

QUANTITY = sa.Numeric(14, 3)  # exact decimal quantities everywhere (D4)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'staff')", name="ck_users_role"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)

    op.create_table(
        "units",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("abbreviation", sa.String(10), nullable=False),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("abbreviation"),
    )

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("contact_name", sa.String(120), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_suppliers_status"),
    )

    # Created WITHOUT the composite FK to supplier_ingredients (added below).
    op.create_table(
        "ingredients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("unit_id", sa.Integer, sa.ForeignKey("units.id"), nullable=False),
        sa.Column("min_safety_stock", QUANTITY, nullable=False),
        sa.Column("critical_threshold", QUANTITY, nullable=False),
        sa.Column("usage_per_serving", QUANTITY, nullable=False),
        sa.Column("primary_supplier_id", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_ingredients_status"),
    )
    op.create_index("ix_ingredients_category", "ingredients", ["category"])
    op.create_index("ix_ingredients_status", "ingredients", ["status"])

    op.create_table(
        "supplier_ingredients",
        sa.Column(
            "supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), primary_key=True
        ),
        sa.Column(
            "ingredient_id", sa.Integer, sa.ForeignKey("ingredients.id"), primary_key=True
        ),
        sa.Column("package_size", QUANTITY, nullable=True),
        sa.Column("min_order_quantity", QUANTITY, nullable=True),
    )
    op.create_index(
        "ix_supplier_ingredients_ingredient_id", "supplier_ingredients", ["ingredient_id"]
    )

    # Circular FK added only after BOTH tables exist (D24). Batch mode makes
    # this valid on SQLite (table recreate) and PostgreSQL (ALTER) alike.
    with op.batch_alter_table("ingredients") as batch_op:
        batch_op.create_foreign_key(
            "fk_ingredients_primary_supplier",
            "supplier_ingredients",
            ["primary_supplier_id", "id"],
            ["supplier_id", "ingredient_id"],
        )

    op.create_table(
        "ingredient_stock_state",
        sa.Column(
            "ingredient_id", sa.Integer, sa.ForeignKey("ingredients.id"), primary_key=True
        ),
        sa.Column("current_quantity", QUANTITY, nullable=False),
        sa.Column("last_counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_counted_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("last_transaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unresolved_discrepancy", sa.Boolean, nullable=False),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')", name="ck_stock_state_confidence"
        ),
    )

    op.create_table(
        "inventory_transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "ingredient_id", sa.Integer, sa.ForeignKey("ingredients.id"), nullable=False
        ),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("quantity_delta", QUANTITY, nullable=False),
        sa.Column("resulting_quantity", QUANTITY, nullable=False),
        sa.Column("reason", sa.String(40), nullable=True),
        sa.Column("mode", sa.String(10), nullable=True),
        sa.Column("entered_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("business_date", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.Integer,
            sa.ForeignKey("inventory_transactions.id"),
            nullable=True,
        ),
        sa.UniqueConstraint("idempotency_key"),
        sa.CheckConstraint(
            "type IN ('opening_balance', 'consumption', 'reconciliation', "
            "'delivery_receipt', 'correction', 'adjustment')",
            name="ck_inventory_transactions_type",
        ),
        sa.CheckConstraint(
            "mode IS NULL OR mode IN ('remaining', 'used')",
            name="ck_inventory_transactions_mode",
        ),
    )
    op.create_index(
        "ix_inventory_transactions_ingredient_id",
        "inventory_transactions",
        ["ingredient_id", "id"],
    )
    op.create_index(
        "ix_inventory_transactions_business_date",
        "inventory_transactions",
        ["business_date"],
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=True),
        sa.Column("before_json", sa.Text, nullable=True),
        sa.Column("after_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value_json", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("audit_logs")
    op.drop_table("inventory_transactions")
    op.drop_table("ingredient_stock_state")
    with op.batch_alter_table("ingredients") as batch_op:
        batch_op.drop_constraint("fk_ingredients_primary_supplier", type_="foreignkey")
    op.drop_table("supplier_ingredients")
    op.drop_table("ingredients")
    op.drop_table("suppliers")
    op.drop_table("units")
    op.drop_table("sessions")
    op.drop_table("users")
