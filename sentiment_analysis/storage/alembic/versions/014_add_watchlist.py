"""014 — create watchlist table

Revision ID: 014
Revises: 013
"""
from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlist",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", name="uq_watchlist_ticker"),
    )
    op.create_index("ix_watchlist_ticker", "watchlist", ["ticker"])


def downgrade() -> None:
    op.drop_index("ix_watchlist_ticker", table_name="watchlist")
    op.drop_table("watchlist")
