"""Create alerts and alert_history tables

Revision ID: 019
Revises: 018
Create Date: 2026-07-24

Phase 1 alert system — price and sentiment alerts only.
alert_history.alert_id FK uses ON DELETE CASCADE so removing an alert
automatically removes all of its history rows.
"""
from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id",         sa.Integer(),     nullable=False, autoincrement=True),
        sa.Column("ticker",     sa.String(20),    nullable=False),
        sa.Column("alert_type", sa.String(20),    nullable=False),
        sa.Column("operator",   sa.String(5),     nullable=False),
        sa.Column("threshold",  sa.Float(),       nullable=False),
        sa.Column("is_active",  sa.Boolean(),     nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_ticker",    "alerts", ["ticker"])
    op.create_index("ix_alerts_is_active", "alerts", ["is_active"])

    op.create_table(
        "alert_history",
        sa.Column("id",            sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("alert_id",      sa.Integer(), nullable=False),
        sa.Column("ticker",        sa.String(20), nullable=False),
        sa.Column("trigger_value", sa.Float(),    nullable=False),
        sa.Column("triggered_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("message",       sa.Text(),    nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["alert_id"], ["alerts.id"],
            name="fk_alert_history_alert_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_alert_history_alert_id",     "alert_history", ["alert_id"])
    op.create_index("ix_alert_history_triggered_at", "alert_history", ["triggered_at"])
    op.create_index("ix_alert_history_ticker",       "alert_history", ["ticker"])


def downgrade() -> None:
    op.drop_index("ix_alert_history_ticker",       table_name="alert_history")
    op.drop_index("ix_alert_history_triggered_at", table_name="alert_history")
    op.drop_index("ix_alert_history_alert_id",     table_name="alert_history")
    op.drop_table("alert_history")

    op.drop_index("ix_alerts_is_active", table_name="alerts")
    op.drop_index("ix_alerts_ticker",    table_name="alerts")
    op.drop_table("alerts")
