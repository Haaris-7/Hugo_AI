"""Add composable compensation, QA stages, platforms, and Telegram approvals."""

import sqlalchemy as sa

from alembic import op

revision = "20260629_05"
down_revision = "20260629_04"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _uniques(table: str) -> list[dict]:
    return sa.inspect(op.get_bind()).get_unique_constraints(table)


def upgrade() -> None:
    campaign_columns = _columns("campaigns")
    with op.batch_alter_table("campaigns") as batch:
        additions = {
            "compensation": sa.Column(
                "compensation", sa.JSON(), server_default="{}", nullable=False
            ),
            "compensation_source": sa.Column(
                "compensation_source", sa.String(30), server_default="legacy", nullable=False
            ),
            "compensation_locked": sa.Column(
                "compensation_locked", sa.Boolean(), server_default=sa.false(), nullable=False
            ),
            "operation_mode": sa.Column(
                "operation_mode", sa.String(50), server_default="strategy_creators", nullable=False
            ),
            "measurement_window_hours": sa.Column(
                "measurement_window_hours", sa.Integer(), server_default="72", nullable=False
            ),
            "qa_mode": sa.Column("qa_mode", sa.String(20), server_default="legacy", nullable=False),
        }
        for name, column in additions.items():
            if name not in campaign_columns:
                batch.add_column(column)

    deal_columns = _columns("deals")
    with op.batch_alter_table("deals") as batch:
        additions = {
            "compensation": sa.Column(
                "compensation", sa.JSON(), server_default="{}", nullable=False
            ),
            "draft_approved": sa.Column(
                "draft_approved", sa.Boolean(), server_default=sa.false(), nullable=False
            ),
            "final_approved": sa.Column(
                "final_approved", sa.Boolean(), server_default=sa.false(), nullable=False
            ),
            "replacement_attempt": sa.Column(
                "replacement_attempt", sa.Integer(), server_default="0", nullable=False
            ),
            "replacement_for_id": sa.Column(
                "replacement_for_id",
                sa.String(36),
                sa.ForeignKey("deals.id", name="fk_deals_replacement_for_id_deals"),
                nullable=True,
            ),
        }
        for name, column in additions.items():
            if name not in deal_columns:
                batch.add_column(column)

    if "stage" not in _columns("deliverables"):
        op.add_column(
            "deliverables",
            sa.Column("stage", sa.String(20), server_default="final", nullable=False),
        )
    if "scheduled_at" not in _columns("outbox_jobs"):
        op.add_column("outbox_jobs", sa.Column("scheduled_at", sa.DateTime(timezone=True)))

    creator_uniques = _uniques("creators")
    handle_unique = next(
        (item for item in creator_uniques if item.get("column_names") == ["handle"]), None
    )
    composite_exists = any(
        item.get("column_names") == ["platform", "handle"] for item in creator_uniques
    )
    naming = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table("creators", naming_convention=naming) as batch:
        if handle_unique:
            batch.drop_constraint(handle_unique.get("name") or "uq_creators_handle", type_="unique")
        if not composite_exists:
            batch.create_unique_constraint(
                "uq_creator_platform_handle", ["platform", "handle"]
            )

    tables = _tables()
    if "approval_requests" not in tables:
        op.create_table(
            "approval_requests",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id"), nullable=False),
            sa.Column("resource_type", sa.String(40), nullable=False),
            sa.Column("resource_id", sa.String(36), nullable=False),
            sa.Column("token", sa.String(32), nullable=False, unique=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("decision_source", sa.String(30)),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True)),
            sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_approval_requests_campaign_id", "approval_requests", ["campaign_id"])
        op.create_index("ix_approval_requests_resource_id", "approval_requests", ["resource_id"])
        op.create_index("ix_approval_requests_token", "approval_requests", ["token"], unique=True)
    if "messaging_channels" not in tables:
        op.create_table(
            "messaging_channels",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("provider", sa.String(30), nullable=False, unique=True),
            sa.Column("chat_id", sa.String(100)),
            sa.Column("user_id", sa.String(100)),
            sa.Column("username", sa.String(150)),
            sa.Column("pairing_nonce", sa.String(64)),
            sa.Column("pairing_expires_at", sa.DateTime(timezone=True)),
            sa.Column("last_update_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "messaging_receipts" not in tables:
        op.create_table(
            "messaging_receipts",
            sa.Column("id", sa.String(150), primary_key=True),
            sa.Column("provider", sa.String(30), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(30), nullable=False, server_default="received"),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    for table in ("messaging_receipts", "messaging_channels", "approval_requests"):
        if table in _tables():
            op.drop_table(table)
