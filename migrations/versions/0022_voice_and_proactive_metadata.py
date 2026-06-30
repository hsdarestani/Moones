"""voice and proactive metadata

Revision ID: 0022_voice_and_proactive_metadata
Revises: 0021_admin_live_dashboard_refactor
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_voice_and_proactive_metadata"
down_revision = "0021_admin_live_dashboard_refactor"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    cols = _cols("messages")
    additions = [
        ("telegram_message_id", sa.Column("telegram_message_id", sa.Integer(), nullable=True)),
        ("telegram_reply_to_message_id", sa.Column("telegram_reply_to_message_id", sa.Integer(), nullable=True)),
        ("input_type", sa.Column("input_type", sa.String(length=32), nullable=False, server_default="text")),
        ("audio_file_id", sa.Column("audio_file_id", sa.String(length=256), nullable=True)),
        ("audio_duration", sa.Column("audio_duration", sa.Integer(), nullable=True)),
        ("transcript_confidence", sa.Column("transcript_confidence", sa.Float(), nullable=True)),
        ("transcription_provider", sa.Column("transcription_provider", sa.String(length=64), nullable=True)),
    ]
    for name, col in additions:
        if name not in cols:
            op.add_column("messages", col)
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_telegram_message_id ON messages (telegram_message_id)")
    op.alter_column("messages", "input_type", server_default=None)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_telegram_message_id")
    for name in ["transcription_provider", "transcript_confidence", "audio_duration", "audio_file_id", "input_type", "telegram_reply_to_message_id", "telegram_message_id"]:
        if name in _cols("messages"):
            op.drop_column("messages", name)
