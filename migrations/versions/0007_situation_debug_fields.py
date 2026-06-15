"""situation debug fields

Revision ID: 0007_situation_debug_fields
Revises: 0006_persian_first_quality_routing
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_situation_debug_fields"
down_revision = "0006_persian_first_quality_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_detected_situation", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("last_fallback_reason", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_context_messages_used", sa.Text(), nullable=True))
    op.alter_column("users", "last_fallback_used", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "last_context_messages_used")
    op.drop_column("users", "last_fallback_reason")
    op.drop_column("users", "last_fallback_used")
    op.drop_column("users", "last_detected_situation")
