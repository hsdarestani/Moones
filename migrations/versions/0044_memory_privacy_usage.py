"""User-controlled semantic consent and accurate nullable embedding metering.

Revision ID: 0044_memory_privacy_usage
Revises: 0043_memory_semantic_index
"""
from alembic import op
import sqlalchemy as sa

revision = "0044_memory_privacy_usage"
down_revision = "0043_memory_semantic_index"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_privacy_choices",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("semantic_opt_in", sa.Boolean(), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "memory_embedding_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_kind", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("provider_cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_memory_embedding_usage_user_created", "memory_embedding_usage", ["user_id", "created_at"])


def downgrade():
    op.drop_index("ix_memory_embedding_usage_user_created", table_name="memory_embedding_usage")
    op.drop_table("memory_embedding_usage")
    op.drop_table("memory_privacy_choices")
