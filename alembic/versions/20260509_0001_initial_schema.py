"""initial schema

Revision ID: 20260509_0001
Revises:
Create Date: 2026-05-09
"""

from alembic import op
import sqlalchemy as sa

revision = "20260509_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("market_microstructure_state", sa.Column("ticker", sa.String(length=128), primary_key=True), sa.Column("spread_history_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("midpoint_history_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("liquidity_history_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("fill_probability", sa.Float(), nullable=False, server_default="0"), sa.Column("replenishment_rate", sa.Float(), nullable=False, server_default="0"), sa.Column("last_seen", sa.Float(), nullable=False, server_default="0"), sa.Column("stale_cycles", sa.Integer(), nullable=False, server_default="0"), sa.Column("execution_score", sa.Float(), nullable=False, server_default="0"), sa.Column("volatility_score", sa.Float(), nullable=False, server_default="0"), sa.Column("status", sa.String(length=16), nullable=False, server_default="inactive"), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_market_microstructure_state_last_seen", "market_microstructure_state", ["last_seen"])
    op.create_index("ix_market_microstructure_state_status", "market_microstructure_state", ["status"])


def downgrade() -> None:
    op.drop_index("ix_market_microstructure_state_status", table_name="market_microstructure_state")
    op.drop_index("ix_market_microstructure_state_last_seen", table_name="market_microstructure_state")
    op.drop_table("market_microstructure_state")
