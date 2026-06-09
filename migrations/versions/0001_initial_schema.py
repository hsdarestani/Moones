"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)
    op.create_table(
        "memory_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("importance_score", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_memory_items_created_at"), "memory_items", ["created_at"], unique=False)
    op.create_index(op.f("ix_memory_items_id"), "memory_items", ["id"], unique=False)
    op.create_index(op.f("ix_memory_items_type"), "memory_items", ["type"], unique=False)
    op.create_index(op.f("ix_memory_items_user_id"), "memory_items", ["user_id"], unique=False)
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("emotion", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messages_created_at"), "messages", ["created_at"], unique=False)
    op.create_index(op.f("ix_messages_id"), "messages", ["id"], unique=False)
    op.create_index(op.f("ix_messages_user_id"), "messages", ["user_id"], unique=False)
    op.create_table(
        "relationships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("intimacy", sa.Float(), nullable=False),
        sa.Column("attachment", sa.Float(), nullable=False),
        sa.Column("trust", sa.Float(), nullable=False),
        sa.Column("dependency", sa.Float(), nullable=False),
        sa.Column("attraction", sa.Float(), nullable=False),
        sa.Column("volatility", sa.Float(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("daily_streak", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_relationships_id"), "relationships", ["id"], unique=False)
    op.create_index(op.f("ix_relationships_user_id"), "relationships", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_relationships_user_id"), table_name="relationships")
    op.drop_index(op.f("ix_relationships_id"), table_name="relationships")
    op.drop_table("relationships")
    op.drop_index(op.f("ix_messages_user_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_created_at"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_memory_items_user_id"), table_name="memory_items")
    op.drop_index(op.f("ix_memory_items_type"), table_name="memory_items")
    op.drop_index(op.f("ix_memory_items_id"), table_name="memory_items")
    op.drop_index(op.f("ix_memory_items_created_at"), table_name="memory_items")
    op.drop_table("memory_items")
    op.drop_index(op.f("ix_users_telegram_id"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
