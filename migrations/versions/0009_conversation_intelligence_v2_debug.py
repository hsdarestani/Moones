"""conversation intelligence v2 debug fields

Revision ID: 0009_conversation_intelligence_v2_debug
Revises: 0008_latency_debug_fields
Create Date: 2026-06-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_conversation_intelligence_v2_debug"
down_revision = "0008_latency_debug_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_context_reset", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("last_safety_flag", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("users", "last_context_reset", server_default=None)
    op.alter_column("users", "last_safety_flag", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "last_safety_flag")
    op.drop_column("users", "last_context_reset")
