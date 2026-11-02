"""Versioned, source-grounded temporal memories.

Revision ID: 0042_temporal_memory_v2
Revises: 0041_adult_anatomical_profile

No chat history is rewritten, deleted or backfilled.
"""
from alembic import op
import sqlalchemy as sa

revision = "0042_temporal_memory_v2"
down_revision = "0041_adult_anatomical_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_memory_events_user_id", "memory_events", ["user_id"])
    op.create_index("ix_memory_events_source_message_id", "memory_events", ["source_message_id"])
    op.create_table(
        "memory_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_event_id", sa.Integer(), sa.ForeignKey("memory_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_key", sa.String(80), nullable=False),
        sa.Column("predicate", sa.String(80), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_to", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_memory_facts_user_slot_valid", "memory_facts",
        ["user_id", "subject_key", "predicate", "valid_to"]
    )
    op.create_index(
        "uq_memory_facts_active_slot", "memory_facts",
        ["user_id", "subject_key", "predicate"], unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
        sqlite_where=sa.text("valid_to IS NULL"),
    )


def downgrade():
    op.drop_index("uq_memory_facts_active_slot", table_name="memory_facts")
    op.drop_index("ix_memory_facts_user_slot_valid", table_name="memory_facts")
    op.drop_table("memory_facts")
    op.drop_index("ix_memory_events_source_message_id", table_name="memory_events")
    op.drop_index("ix_memory_events_user_id", table_name="memory_events")
    op.drop_table("memory_events")
