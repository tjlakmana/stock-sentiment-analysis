"""007 — create ticker_sentiment_summary table

Revision ID: 007
Revises: 006
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticker_sentiment_summary",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("window", sa.String(10), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("avg_sentiment", sa.Float(), nullable=True),
        sa.Column("article_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bullish_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bearish_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("neutral_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("momentum", sa.String(20), nullable=True),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tss_ticker_window", "ticker_sentiment_summary", ["ticker", "window"])
    op.create_index("ix_tss_calculated_at", "ticker_sentiment_summary", ["calculated_at"])


def downgrade() -> None:
    op.drop_index("ix_tss_calculated_at", table_name="ticker_sentiment_summary")
    op.drop_index("ix_tss_ticker_window", table_name="ticker_sentiment_summary")
    op.drop_table("ticker_sentiment_summary")
