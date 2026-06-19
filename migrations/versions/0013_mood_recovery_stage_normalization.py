"""mood recovery and canonical relationship stages

Revision ID: 0013_mood_recovery_stage_normalization
Revises: 0012_token_plans_voice_stickers
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_mood_recovery_stage_normalization"
down_revision = "0012_token_plans_voice_stickers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_mood", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("last_mood_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("consecutive_cold_replies", sa.Integer(), nullable=False, server_default="0"))
    op.execute("UPDATE relationships SET stage='WARM' WHERE stage IN ('FAMILIAR','ACQUAINTANCE')")
    op.execute("UPDATE relationships SET stage='CLOSE' WHERE stage='FRIEND'")
    op.execute("UPDATE relationships SET stage='PARTNER' WHERE stage='ROMANTIC'")
    op.execute("UPDATE relationships SET stage='LOVER' WHERE stage IN ('INTIMATE','BONDED')")
    op.alter_column("users", "consecutive_cold_replies", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "consecutive_cold_replies")
    op.drop_column("users", "last_mood_at")
    op.drop_column("users", "last_mood")
