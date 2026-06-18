"""009 — create ticker_prices table

Revision ID: 009
Revises: 008
"""
from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticker_prices",
        sa.Column("ticker",            sa.Text(),                 nullable=False),
        sa.Column("price",             sa.Float(),                nullable=True),
        sa.Column("change_pct",        sa.Float(),                nullable=True),
        sa.Column("volume",            sa.BigInteger(),           nullable=True),
        sa.Column("market_cap",        sa.BigInteger(),           nullable=True),
        sa.Column("pre_market_price",  sa.Float(),                nullable=True),
        sa.Column("post_market_price", sa.Float(),                nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("ticker"),
    )


def downgrade() -> None:
    op.drop_table("ticker_prices")
