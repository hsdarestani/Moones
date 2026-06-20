"""patch9 soft upsell tracking

Revision ID: 0017_patch9_soft_upsell
Revises: 0016_user_next_proactive_at
Create Date: 2026-06-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_patch9_soft_upsell"
down_revision = "0016_user_next_proactive_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_soft_upsell_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_soft_upsell_at")
