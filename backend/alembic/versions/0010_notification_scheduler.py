"""Add automatic-evaluation settings and persistent worker execution state.

Revision ID: 0010_notification_scheduler
Revises: 0009_notification_center
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_notification_scheduler"
down_revision = "0009_notification_center"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_settings", sa.Column(
        "automatic_evaluation_enabled", sa.Boolean(), nullable=False,
        server_default=sa.false()))
    op.add_column("notification_settings", sa.Column(
        "evaluation_interval_seconds", sa.Integer(), nullable=False,
        server_default="60"))
    op.create_table(
        "notification_worker_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_token", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column("last_result_json", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("notification_worker_state")
    op.drop_column("notification_settings", "evaluation_interval_seconds")
    op.drop_column("notification_settings", "automatic_evaluation_enabled")
