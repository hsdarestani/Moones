"""time aware roleplay

Revision ID: 0029_time_aware_roleplay
Revises: 0028_contextual_sticker_catalog
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_time_aware_roleplay"
down_revision = "0028_contextual_sticker_catalog"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table_name)}

def _indexes(table_name: str) -> set[str]:
    return {idx["name"] for idx in sa.inspect(op.get_bind()).get_indexes(table_name)}

def upgrade() -> None:
    user_cols = _columns("users")
    with op.batch_alter_table("users") as batch:
        if "timezone_name" not in user_cols:
            batch.add_column(sa.Column("timezone_name", sa.String(length=64), nullable=True))
        if "timezone_source" not in user_cols:
            batch.add_column(sa.Column("timezone_source", sa.String(length=32), nullable=True))
        if "last_user_message_at" not in user_cols:
            batch.add_column(sa.Column("last_user_message_at", sa.DateTime(), nullable=True))
        if "last_assistant_message_at" not in user_cols:
            batch.add_column(sa.Column("last_assistant_message_at", sa.DateTime(), nullable=True))
        if "last_gap_bucket" not in user_cols:
            batch.add_column(sa.Column("last_gap_bucket", sa.String(length=32), nullable=True))
    op.execute("UPDATE users SET timezone_name = 'Asia/Tehran', timezone_source = COALESCE(timezone_source, 'default') WHERE timezone_name IS NULL")
    op.execute("UPDATE users SET last_user_message_at = (SELECT MAX(created_at) FROM messages WHERE messages.user_id = users.id AND role = 'user') WHERE last_user_message_at IS NULL")
    op.execute("UPDATE users SET last_assistant_message_at = (SELECT MAX(created_at) FROM messages WHERE messages.user_id = users.id AND role = 'assistant') WHERE last_assistant_message_at IS NULL")
    if "ix_messages_user_id_created_at" not in _indexes("messages"):
        op.create_index("ix_messages_user_id_created_at", "messages", ["user_id", "created_at"])
    if "partner_daily_routines" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "partner_daily_routines",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("local_date", sa.Date(), nullable=False),
            sa.Column("timezone_name", sa.String(length=64), nullable=False),
            sa.Column("city", sa.String(length=120), nullable=False),
            sa.Column("schedule_json", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="deterministic"),
            sa.Column("prompt_version", sa.String(length=32), nullable=False, server_default="routine_v1"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "local_date", name="uq_partner_daily_routine_user_date"),
        )
        op.create_index("ix_partner_daily_routines_user_id", "partner_daily_routines", ["user_id"])
        op.create_index("ix_partner_daily_routines_local_date", "partner_daily_routines", ["local_date"])
        op.create_index("ix_partner_daily_routines_created_at", "partner_daily_routines", ["created_at"])

def downgrade() -> None:
    if "partner_daily_routines" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("partner_daily_routines")
    if "ix_messages_user_id_created_at" in _indexes("messages"):
        op.drop_index("ix_messages_user_id_created_at", table_name="messages")
    cols = _columns("users")
    with op.batch_alter_table("users") as batch:
        for name in ["last_gap_bucket", "last_assistant_message_at", "last_user_message_at", "timezone_source", "timezone_name"]:
            if name in cols:
                batch.drop_column(name)
