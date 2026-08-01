"""Create ticker_snapshot_history table

Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Revision ID: 018
Revises: 017
Create Date: 2026-07-24

Stores one Finviz snapshot per ticker per calendar day (ET).
The UNIQUE constraint on (ticker, snapshot_date) means subsequent ingestor
runs on the same day are no-ops via INSERT ... ON CONFLICT DO NOTHING.

ticker_prices is not modified — it continues to hold the current/live snapshot.
"""
from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticker_snapshot_history",
        sa.Column("id",            sa.Integer(),   nullable=False, autoincrement=True),
        sa.Column("ticker",        sa.Text(),      nullable=False),
        sa.Column("snapshot_date", sa.Date(),      nullable=False),

        # Core price
        sa.Column("price",         sa.Float(),     nullable=True),
        sa.Column("market_cap",    sa.BigInteger(), nullable=True),

        # Valuation
        sa.Column("pe",            sa.Float(),     nullable=True),
        sa.Column("forward_pe",    sa.Float(),     nullable=True),
        sa.Column("peg",           sa.Float(),     nullable=True),
        sa.Column("price_book",    sa.Float(),     nullable=True),
        sa.Column("price_sales",   sa.Float(),     nullable=True),

        # Profitability
        sa.Column("gross_margin",  sa.Float(),     nullable=True),
        sa.Column("net_margin",    sa.Float(),     nullable=True),
        sa.Column("roe",           sa.Float(),     nullable=True),
        sa.Column("roa",           sa.Float(),     nullable=True),

        # Financial health
        sa.Column("current_ratio", sa.Float(),     nullable=True),
        sa.Column("debt_equity",   sa.Float(),     nullable=True),

        # Growth
        sa.Column("eps_growth_this_year", sa.Float(), nullable=True),
        sa.Column("eps_growth_next_year", sa.Float(), nullable=True),
        sa.Column("eps_growth_5y",        sa.Float(), nullable=True),

        # Technical
        sa.Column("rsi_14",        sa.Float(),     nullable=True),
        sa.Column("sma_20_pct",    sa.Float(),     nullable=True),
        sa.Column("sma_50_pct",    sa.Float(),     nullable=True),
        sa.Column("sma_200_pct",   sa.Float(),     nullable=True),
        sa.Column("atr",           sa.Float(),     nullable=True),
        sa.Column("rel_volume",    sa.Float(),     nullable=True),

        # Short interest
        sa.Column("short_float",   sa.Float(),     nullable=True),
        sa.Column("short_ratio",   sa.Float(),     nullable=True),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),

        sa.PrimaryKeyConstraint("id"),
    )

    # Enforce one snapshot per ticker per calendar day
    op.create_index(
        "uq_snapshot_ticker_date",
        "ticker_snapshot_history",
        ["ticker", "snapshot_date"],
        unique=True,
    )
    op.create_index("ix_snapshot_ticker", "ticker_snapshot_history", ["ticker"])
    op.create_index("ix_snapshot_date",   "ticker_snapshot_history", ["snapshot_date"])


def downgrade() -> None:
    op.drop_index("ix_snapshot_date",         table_name="ticker_snapshot_history")
    op.drop_index("ix_snapshot_ticker",       table_name="ticker_snapshot_history")
    op.drop_index("uq_snapshot_ticker_date",  table_name="ticker_snapshot_history")
    op.drop_table("ticker_snapshot_history")
