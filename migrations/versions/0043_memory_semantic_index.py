"""Opt-in semantic index without requiring pgvector extension.

Revision ID: 0043_memory_semantic_index
Revises: 0042_temporal_memory_v2

Additive migration; does not read or transmit any user content.
"""
from alembic import op
import sqlalchemy as sa

revision = "0043_memory_semantic_index"
down_revision = "0042_temporal_memory_v2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "source_message_id", "model_id", name="uq_memory_embeddings_source_model"),
    )
    op.create_index("ix_memory_embeddings_user_model", "memory_embeddings", ["user_id", "model_id"])


def downgrade():
    op.drop_index("ix_memory_embeddings_user_model", table_name="memory_embeddings")
    op.drop_table("memory_embeddings")
