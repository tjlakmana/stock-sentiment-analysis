"""Add condition_met to alerts

Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Revision ID: 020
Revises: 019
Create Date: 2026-07-24

Tracks whether an alert's condition is currently true so the scheduler
only fires on the False→True transition (condition first becomes met).
When the condition clears the column reverts to FALSE, allowing the alert
to fire again the next time the condition is met.
"""
from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column(
            "condition_met",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )


def downgrade() -> None:
    op.drop_column("alerts", "condition_met")
