"""mood delivery state

Revision ID: 0011_mood_delivery
Revises: 0010_llm_pipeline_debug_qwen_defaults
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_mood_delivery"
down_revision = "0010_llm_pipeline_debug_qwen_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("current_mood", sa.String(length=32), nullable=False, server_default="warm"))
    op.add_column("users", sa.Column("affection_score", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("trust_score", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("irritation_score", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("playfulness_score", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("last_voice_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("last_sticker_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("last_rude_message_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("last_delivery_type", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("consecutive_text_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("consecutive_voice_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("consecutive_sticker_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for col in ("consecutive_sticker_count", "consecutive_voice_count", "consecutive_text_count", "last_delivery_type", "last_rude_message_at", "last_sticker_at", "last_voice_at", "playfulness_score", "irritation_score", "trust_score", "affection_score", "current_mood"):
        op.drop_column("users", col)
