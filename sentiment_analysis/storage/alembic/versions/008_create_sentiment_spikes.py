"""008 — create sentiment_spikes table

Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Revision ID: 008
Revises: 007
"""
from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sentiment_spikes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("article_count", sa.Integer(), nullable=False),
        sa.Column("rolling_avg", sa.Float(), nullable=False),
        sa.Column("spike_ratio", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sentiment_spikes_ticker", "sentiment_spikes", ["ticker"])
    op.create_index("ix_sentiment_spikes_detected_at", "sentiment_spikes", ["detected_at"])


def downgrade() -> None:
    op.drop_index("ix_sentiment_spikes_detected_at", table_name="sentiment_spikes")
    op.drop_index("ix_sentiment_spikes_ticker", table_name="sentiment_spikes")
    op.drop_table("sentiment_spikes")
