"""012 — drop importance_score and importance_label from rss_articles

Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Revision ID: 012
Revises: 011
"""
from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_rss_articles_importance_score", table_name="rss_articles")
    op.drop_column("rss_articles", "importance_label")
    op.drop_column("rss_articles", "importance_score")


def downgrade() -> None:
    op.add_column(
        "rss_articles",
        sa.Column("importance_score", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "rss_articles",
        sa.Column("importance_label", sa.String(10), nullable=True),
    )
    op.create_index(
        "ix_rss_articles_importance_score",
        "rss_articles",
        ["importance_score"],
    )
