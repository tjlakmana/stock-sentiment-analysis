"""Add roa, gross_margin, operating_margin, net_margin, beta to ticker_prices

Revision ID: 016
Revises: 015
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticker_prices", sa.Column("roa",              sa.Float(), nullable=True))
    op.add_column("ticker_prices", sa.Column("gross_margin",     sa.Float(), nullable=True))
    op.add_column("ticker_prices", sa.Column("operating_margin", sa.Float(), nullable=True))
    op.add_column("ticker_prices", sa.Column("net_margin",       sa.Float(), nullable=True))
    op.add_column("ticker_prices", sa.Column("beta",             sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("ticker_prices", "beta")
    op.drop_column("ticker_prices", "net_margin")
    op.drop_column("ticker_prices", "operating_margin")
    op.drop_column("ticker_prices", "gross_margin")
    op.drop_column("ticker_prices", "roa")
