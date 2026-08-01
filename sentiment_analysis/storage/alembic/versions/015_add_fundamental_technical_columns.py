"""015 — add fundamental and technical columns to ticker_prices

Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Revision ID: 015
Revises: 014
"""
from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

_NEW_FLOAT_COLS = [
    "pe_ratio",
    "forward_pe",
    "peg_ratio",
    "price_to_sales",
    "price_to_book",
    "dividend_yield",
    "eps_ttm",
    "roe",
    "debt_to_equity",
    "current_ratio",
    "quick_ratio",
    "rel_volume",
    "rsi_14",
    "sma_20_pct",
    "sma_50_pct",
    "sma_200_pct",
    "week_52_high_pct",
    "week_52_low_pct",
]

_NEW_BIGINT_COLS = [
    "avg_volume",
]


def upgrade() -> None:
    for col in _NEW_FLOAT_COLS:
        op.add_column("ticker_prices", sa.Column(col, sa.Float(), nullable=True))
    for col in _NEW_BIGINT_COLS:
        op.add_column("ticker_prices", sa.Column(col, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    for col in reversed(_NEW_BIGINT_COLS + _NEW_FLOAT_COLS):
        op.drop_column("ticker_prices", col)
