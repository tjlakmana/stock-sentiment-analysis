"""Add last_seen_at to alerts

Revision ID: 021
Revises: 020
Create Date: 2026-07-24

High-water-mark timestamp used by breaking_news and volume_spike alert
types to track the most recent article / spike that has already been
processed.  The server_default of NOW() means newly-created alerts of
these types start from the moment of creation, so old data never triggers.

price and sentiment alerts ignore this column (they use condition_met).
"""
from alembic import op
import sqlalchemy as sa

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_column("alerts", "last_seen_at")
