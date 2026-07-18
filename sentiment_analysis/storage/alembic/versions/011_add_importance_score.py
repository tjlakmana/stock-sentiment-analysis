"""011 — add importance_score and importance_label to rss_articles

Revision ID: 011
Revises: 010
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_rss_articles_importance_score", table_name="rss_articles")
    op.drop_column("rss_articles", "importance_label")
    op.drop_column("rss_articles", "importance_score")
