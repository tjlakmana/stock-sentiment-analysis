"""Add Tier 1 screener fields: short interest, performance, analyst, growth, ATR, ownership

Revision ID: 017
Revises: 016
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None

_NEW_FLOAT_COLS = [
    "float_short",
    "short_ratio",
    "perf_week",
    "perf_month",
    "perf_quart",
    "perf_year",
    "perf_ytd",
    "analyst_recom",
    "target_price",
    "eps_growth_this_year",
    "eps_growth_next_year",
    "eps_growth_5y",
    "eps_growth_qoq",
    "sales_growth_qoq",
    "atr",
    "insider_own",
    "inst_own",
]


def upgrade() -> None:
    for col in _NEW_FLOAT_COLS:
        op.add_column("ticker_prices", sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in reversed(_NEW_FLOAT_COLS):
        op.drop_column("ticker_prices", col)
