"""010 — add company info columns to ticker_prices

Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Revision ID: 010
Revises: 009
"""
from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticker_prices", sa.Column("company_name", sa.Text(),        nullable=True))
    op.add_column("ticker_prices", sa.Column("sector",       sa.String(100),   nullable=True))
    op.add_column("ticker_prices", sa.Column("country",      sa.String(50),    nullable=True))
    op.add_column("ticker_prices", sa.Column("exchange",     sa.String(20),    nullable=True))


def downgrade() -> None:
    op.drop_column("ticker_prices", "exchange")
    op.drop_column("ticker_prices", "country")
    op.drop_column("ticker_prices", "sector")
    op.drop_column("ticker_prices", "company_name")
