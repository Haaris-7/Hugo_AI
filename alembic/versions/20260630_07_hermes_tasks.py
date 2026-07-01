"""Add hermes_tasks table for durable cron orchestration."""

import sqlalchemy as sa

from alembic import op

revision = "20260630_07"
down_revision = "20260630_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "hermes_tasks" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "hermes_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("deal_id", sa.String(36), sa.ForeignKey("deals.id"), nullable=True),
        sa.Column("task_type", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedupe_key", sa.String(200), unique=True, nullable=True),
        sa.Column("evidence", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_hermes_tasks_status", "hermes_tasks", ["status"])
    op.create_index("ix_hermes_tasks_campaign_id", "hermes_tasks", ["campaign_id"])
    op.create_index("ix_hermes_tasks_deal_id", "hermes_tasks", ["deal_id"])


def downgrade() -> None:
    op.drop_table("hermes_tasks")
