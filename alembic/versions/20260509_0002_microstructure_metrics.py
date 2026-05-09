"""add microstructure scalar metrics

Revision ID: 20260509_0002
Revises: 20260509_0001
Create Date: 2026-05-09
"""

from alembic import op
import sqlalchemy as sa

revision = "20260509_0002"
down_revision = "20260509_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("market_microstructure_state") as batch_op:
        batch_op.add_column(sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=True))
        batch_op.add_column(sa.Column("spread", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("imbalance", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("volatility", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("microprice", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("liquidity_score", sa.Float(), nullable=False, server_default="0"))
    op.create_index("ix_market_microstructure_state_ticker_updated", "market_microstructure_state", ["ticker", "updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_market_microstructure_state_ticker_updated", table_name="market_microstructure_state")
    with op.batch_alter_table("market_microstructure_state") as batch_op:
        batch_op.drop_column("liquidity_score")
        batch_op.drop_column("microprice")
        batch_op.drop_column("volatility")
        batch_op.drop_column("imbalance")
        batch_op.drop_column("spread")
        batch_op.drop_column("id")
