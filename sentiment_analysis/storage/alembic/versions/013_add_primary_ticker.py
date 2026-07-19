"""013 — add primary_ticker to rss_articles

Revision ID: 013
Revises: 012
"""
from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rss_articles",
        sa.Column("primary_ticker", sa.String(20), nullable=True),
    )
    op.create_index(
        "ix_rss_articles_primary_ticker",
        "rss_articles",
        ["primary_ticker"],
    )


def downgrade() -> None:
    op.drop_index("ix_rss_articles_primary_ticker", table_name="rss_articles")
    op.drop_column("rss_articles", "primary_ticker")
