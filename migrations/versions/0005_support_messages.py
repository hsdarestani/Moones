"""add support messages

Revision ID: 0005_support_messages
Revises: 0004_venice_dual_bots_payment_settings_stickers
Create Date: 2026-06-18 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_support_messages"
down_revision = "0004_venice_dual_bots_payment_settings_stickers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("admin_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("admin_message_id", sa.Integer(), nullable=True),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("admin_reply", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("replied_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_support_messages_id"), "support_messages", ["id"], unique=False)
    op.create_index(op.f("ix_support_messages_user_id"), "support_messages", ["user_id"], unique=False)
    op.create_index(op.f("ix_support_messages_user_telegram_id"), "support_messages", ["user_telegram_id"], unique=False)
    op.create_index(op.f("ix_support_messages_admin_telegram_id"), "support_messages", ["admin_telegram_id"], unique=False)
    op.create_index(op.f("ix_support_messages_admin_message_id"), "support_messages", ["admin_message_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_support_messages_admin_message_id"), table_name="support_messages")
    op.drop_index(op.f("ix_support_messages_admin_telegram_id"), table_name="support_messages")
    op.drop_index(op.f("ix_support_messages_user_telegram_id"), table_name="support_messages")
    op.drop_index(op.f("ix_support_messages_user_id"), table_name="support_messages")
    op.drop_index(op.f("ix_support_messages_id"), table_name="support_messages")
    op.drop_table("support_messages")
