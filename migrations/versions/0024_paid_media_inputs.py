"""paid media inputs

Revision ID: 0024_paid_media_inputs
Revises: 0023_human_presence_engine
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0024_paid_media_inputs"
down_revision = "0023_human_presence_engine"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("daily_usage", sa.Column("monthly_image_inputs_used", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("daily_usage", sa.Column("monthly_voice_inputs_used", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "media_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_ref", sa.String(length=64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_file_unique_id", sa.Text(), nullable=True),
        sa.Column("telegram_file_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(), nullable=True),
        sa.Column("stored_path", sa.Text(), nullable=True),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("vision_model", sa.Text(), nullable=True),
        sa.Column("stt_model", sa.Text(), nullable=True),
        sa.Column("support_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("support_message_id", sa.Integer(), nullable=True),
        sa.Column("support_forward_status", sa.String(length=32), nullable=False, server_default="not_sent"),
        sa.Column("support_forward_error", sa.Text(), nullable=True),
        sa.Column("support_forwarded_at", sa.DateTime(), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
    )
    for col in ("media_ref","user_id","message_id","telegram_message_id","telegram_chat_id","kind","support_chat_id","support_message_id","support_forward_status","processing_status","created_at"):
        op.create_index(f"ix_media_messages_{col}", "media_messages", [col])


def downgrade():
    op.drop_table("media_messages")
    op.drop_column("daily_usage", "monthly_voice_inputs_used")
    op.drop_column("daily_usage", "monthly_image_inputs_used")
