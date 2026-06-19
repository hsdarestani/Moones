"""proactive messages

Revision ID: 0014_proactive_messages
Revises: 0013_support_messages
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_proactive_messages"
down_revision = ("0013_support_messages", "0013_production_missing_columns")
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("last_proactive_message_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("proactive_messages_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("proactive_blocked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "proactive_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_proactive_messages_id"), "proactive_messages", ["id"], unique=False)
    op.create_index(op.f("ix_proactive_messages_user_id"), "proactive_messages", ["user_id"], unique=False)
    op.alter_column("users", "proactive_messages_enabled", server_default=None)
    op.alter_column("users", "proactive_blocked", server_default=None)

def downgrade():
    op.drop_index(op.f("ix_proactive_messages_user_id"), table_name="proactive_messages")
    op.drop_index(op.f("ix_proactive_messages_id"), table_name="proactive_messages")
    op.drop_table("proactive_messages")
    op.drop_column("users", "proactive_blocked")
    op.drop_column("users", "proactive_messages_enabled")
    op.drop_column("users", "last_proactive_message_at")
