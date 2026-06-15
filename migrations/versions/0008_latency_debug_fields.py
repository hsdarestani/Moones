"""latency debug fields

Revision ID: 0008_latency_debug_fields
Revises: 0007_situation_debug_fields
Create Date: 2026-06-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_latency_debug_fields"
down_revision = "0007_situation_debug_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_simple_intent_bypass", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("last_latency_breakdown", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_llm_called", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("users", "last_simple_intent_bypass", server_default=None)
    op.alter_column("users", "last_llm_called", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "last_llm_called")
    op.drop_column("users", "last_latency_breakdown")
    op.drop_column("users", "last_simple_intent_bypass")
