"""006 — add sentiment columns to rss_articles

Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Revision ID: 006
Revises: 005
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rss_articles", sa.Column("sentiment_label", sa.Text(), nullable=True))
    op.add_column("rss_articles", sa.Column("sentiment_score", sa.Float(), nullable=True))
    op.add_column("rss_articles", sa.Column("sentiment_confidence", sa.Float(), nullable=True))
    op.add_column(
        "rss_articles",
        sa.Column("sentiment_analyzed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_rss_articles_sentiment_analyzed_at",
        "rss_articles",
        ["sentiment_analyzed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rss_articles_sentiment_analyzed_at", table_name="rss_articles")
    op.drop_column("rss_articles", "sentiment_analyzed_at")
    op.drop_column("rss_articles", "sentiment_confidence")
    op.drop_column("rss_articles", "sentiment_score")
    op.drop_column("rss_articles", "sentiment_label")
