"""add user next proactive schedule

Revision ID: 0016_user_next_proactive_at
Revises: 0015_merge_mood_recovery_and_proactive
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_user_next_proactive_at"
down_revision = "0015_merge_mood_recovery_and_proactive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS next_proactive_at TIMESTAMP NULL")
    else:
        inspector = sa.inspect(bind)
        columns = {c["name"] for c in inspector.get_columns("users")}
        if "next_proactive_at" not in columns:
            op.add_column("users", sa.Column("next_proactive_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "next_proactive_at" in columns:
        op.drop_column("users", "next_proactive_at")
