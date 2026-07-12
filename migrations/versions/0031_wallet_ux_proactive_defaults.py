"""wallet ux proactive defaults

Revision ID: 0031_wallet_ux_proactive_defaults
Revises: 0030_coin_usage_billing
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_wallet_ux_proactive_defaults"
down_revision = "0030_coin_usage_billing"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET proactive_messages_enabled = true WHERE proactive_messages_enabled IS NULL")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("proactive_messages_enabled", existing_type=sa.Boolean(), nullable=False, server_default=sa.true())


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.alter_column("proactive_messages_enabled", existing_type=sa.Boolean(), nullable=True, server_default=None)
