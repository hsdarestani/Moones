"""Add message metadata for billing status.

Revision ID: 0036_low_wallet_message_metadata
Revises: 0035_admin_coin_campaigns
Create Date: 2026-07-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_low_wallet_message_metadata"
down_revision = "0035_admin_coin_campaigns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "metadata")
