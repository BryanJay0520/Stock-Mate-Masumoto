"""Persistent in-app notification foundation; no existing data is changed.

Revision ID: 0009_notification_center
Revises: 0008_ingredient_demand_settings
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_notification_center"
down_revision = "0008_ingredient_demand_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("condition_key", sa.String(190), nullable=False),
        sa.Column("dedupe_key", sa.String(220), nullable=False, unique=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("ingredient_id", sa.Integer(), sa.ForeignKey("ingredients.id")),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id")),
        sa.Column("purchase_order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id")),
        sa.Column("delivery_cycle_key", sa.String(100)),
        sa.Column("business_date", sa.Date()),
        sa.Column("action_at", sa.DateTime(timezone=True)),
        sa.Column("navigation_target", sa.String(220), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("type IN ('daily_count', 'order_cutoff', 'supplier_contact', 'forecast_shortage', 'verify_pending_order', 'pending_delivery')", name="ck_notification_type"),
        sa.CheckConstraint("priority IN ('critical', 'high', 'normal', 'informational')", name="ck_notification_priority"),
        sa.CheckConstraint("status IN ('active', 'expired', 'superseded', 'resolved')", name="ck_notification_status"),
    )
    op.create_index("ix_notification_status_action", "notification_events", ["status", "action_at"])
    op.create_index("ix_notification_business_date", "notification_events", ["business_date"])
    op.create_index("ix_notification_condition_key", "notification_events", ["condition_key"])
    op.create_table(
        "notification_user_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("notification_id", sa.Integer(), sa.ForeignKey("notification_events.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("notification_id", "user_id", name="uq_notification_user"),
    )
    op.create_index("ix_notification_user_states_notification_id", "notification_user_states", ["notification_id"])
    op.create_index("ix_notification_user_states_user_id", "notification_user_states", ["user_id"])
    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("daily_count_enabled", sa.Boolean(), nullable=False),
        sa.Column("daily_count_time", sa.Time(), nullable=False),
        sa.Column("order_cutoff_enabled", sa.Boolean(), nullable=False),
        sa.Column("supplier_contact_enabled", sa.Boolean(), nullable=False),
        sa.Column("forecast_shortage_enabled", sa.Boolean(), nullable=False),
        sa.Column("verify_pending_order_enabled", sa.Boolean(), nullable=False),
        sa.Column("pending_delivery_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "notification_count_rules",
        sa.Column("ingredient_id", sa.Integer(), sa.ForeignKey("ingredients.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("notification_count_rules")
    op.drop_table("notification_settings")
    op.drop_index("ix_notification_user_states_user_id", "notification_user_states")
    op.drop_index("ix_notification_user_states_notification_id", "notification_user_states")
    op.drop_table("notification_user_states")
    op.drop_index("ix_notification_business_date", "notification_events")
    op.drop_index("ix_notification_condition_key", "notification_events")
    op.drop_index("ix_notification_status_action", "notification_events")
    op.drop_table("notification_events")
