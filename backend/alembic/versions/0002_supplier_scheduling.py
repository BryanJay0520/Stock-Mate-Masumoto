"""Supplier scheduling: contact fields on suppliers, supplier_schedule_rules,
supplier_calendar_exceptions (Phase 2 Step 1, decisions D32-D35).

- Suppliers gain general ordering-instruction fields (contact_method,
  ordering_url, ordering_procedures).
- Each supplier owns independent weekly schedule rules; an optional
  ingredient_id makes a rule an additive ingredient-specific override.
- Duplicate ACTIVE rules are prevented by partial unique indexes, which
  both SQLite and PostgreSQL support.
- Calendar exceptions are date-specific rows that never rewrite the
  weekly rules.

Revision ID: 0002_supplier_scheduling
Revises: 0001_initial_schema
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_supplier_scheduling"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

_CONTACT_ACTIONS = ("call", "email", "line", "website_order", "other")
_EXCEPTION_TYPES = (
    "delivery_cancelled",
    "ordering_suspended",
    "cutoff_override",
    "extra_delivery",
)


def upgrade() -> None:
    with op.batch_alter_table("suppliers") as batch_op:
        batch_op.add_column(sa.Column("contact_method", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("ordering_url", sa.String(300), nullable=True))
        batch_op.add_column(sa.Column("ordering_procedures", sa.Text, nullable=True))
        batch_op.create_check_constraint(
            "ck_suppliers_contact_method",
            "contact_method IS NULL OR contact_method IN ('phone', 'line', 'email', 'website', 'other')",
        )

    op.create_table(
        "supplier_schedule_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), nullable=False
        ),
        sa.Column(
            "ingredient_id", sa.Integer, sa.ForeignKey("ingredients.id"), nullable=True
        ),
        sa.Column("delivery_weekday", sa.Integer, nullable=False),
        sa.Column("order_cutoff_days_before", sa.Integer, nullable=False),
        sa.Column("order_cutoff_time", sa.Time, nullable=False),
        sa.Column("order_open_days_before", sa.Integer, nullable=True),
        sa.Column("order_open_time", sa.Time, nullable=True),
        sa.Column("reminder_days_before", sa.Integer, nullable=True),
        sa.Column("reminder_time", sa.Time, nullable=True),
        sa.Column("timezone", sa.String(50), nullable=False),
        sa.Column("contact_action", sa.String(20), nullable=True),
        sa.Column("contact_deadline_days_before", sa.Integer, nullable=True),
        sa.Column("contact_deadline_time", sa.Time, nullable=True),
        sa.Column("contact_required", sa.Boolean, nullable=False),
        sa.Column("special_instructions", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "delivery_weekday BETWEEN 0 AND 6", name="ck_rules_weekday"
        ),
        sa.CheckConstraint(
            "order_cutoff_days_before BETWEEN 1 AND 6", name="ck_rules_cutoff_offset"
        ),
        sa.CheckConstraint(
            "order_open_days_before IS NULL OR order_open_days_before BETWEEN 1 AND 13",
            name="ck_rules_open_offset",
        ),
        sa.CheckConstraint(
            "reminder_days_before IS NULL OR reminder_days_before BETWEEN 0 AND 13",
            name="ck_rules_reminder_offset",
        ),
        sa.CheckConstraint(
            "contact_deadline_days_before IS NULL OR contact_deadline_days_before BETWEEN 0 AND 13",
            name="ck_rules_contact_offset",
        ),
        sa.CheckConstraint(
            "contact_action IS NULL OR contact_action IN ("
            + ", ".join(f"'{a}'" for a in _CONTACT_ACTIONS)
            + ")",
            name="ck_rules_contact_action",
        ),
    )
    op.create_index(
        "ix_supplier_schedule_rules_supplier_id",
        "supplier_schedule_rules",
        ["supplier_id"],
    )
    op.create_index(
        "ix_rules_supplier", "supplier_schedule_rules", ["supplier_id", "is_active"]
    )
    _active_wide = sa.text("ingredient_id IS NULL AND is_active = 1")
    _active_ingredient = sa.text("ingredient_id IS NOT NULL AND is_active = 1")
    op.create_index(
        "uq_rules_supplier_weekday_active",
        "supplier_schedule_rules",
        ["supplier_id", "delivery_weekday"],
        unique=True,
        sqlite_where=_active_wide,
        postgresql_where=_active_wide,
    )
    op.create_index(
        "uq_rules_supplier_weekday_ingredient_active",
        "supplier_schedule_rules",
        ["supplier_id", "delivery_weekday", "ingredient_id"],
        unique=True,
        sqlite_where=_active_ingredient,
        postgresql_where=_active_ingredient,
    )

    op.create_table(
        "supplier_calendar_exceptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), nullable=False
        ),
        sa.Column(
            "schedule_rule_id",
            sa.Integer,
            sa.ForeignKey("supplier_schedule_rules.id"),
            nullable=True,
        ),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("override_cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN (" + ", ".join(f"'{t}'" for t in _EXCEPTION_TYPES) + ")",
            name="ck_exceptions_type",
        ),
    )
    op.create_index(
        "ix_supplier_calendar_exceptions_supplier_id",
        "supplier_calendar_exceptions",
        ["supplier_id"],
    )
    op.create_index(
        "uq_exceptions_supplier_date_type",
        "supplier_calendar_exceptions",
        ["supplier_id", "date", "type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("supplier_calendar_exceptions")
    op.drop_table("supplier_schedule_rules")
    with op.batch_alter_table("suppliers") as batch_op:
        batch_op.drop_constraint("ck_suppliers_contact_method", type_="check")
        batch_op.drop_column("ordering_procedures")
        batch_op.drop_column("ordering_url")
        batch_op.drop_column("contact_method")
