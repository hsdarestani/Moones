"""onboarding admin llm fields

Revision ID: 0002_onboarding_admin_llm_fields
Revises: 0001_initial_schema
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_onboarding_admin_llm_fields"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("onboarding_step", sa.String(length=32), nullable=False, server_default="not_started"))
    op.add_column("users", sa.Column("partner_gender", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("partner_name", sa.String(length=20), nullable=True))
    op.add_column("users", sa.Column("partner_age_range", sa.String(length=16), nullable=True))
    op.add_column("users", sa.Column("partner_personality_type", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("partner_interests", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_prompt", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_llm_response", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_llm_response")
    op.drop_column("users", "last_prompt")
    op.drop_column("users", "partner_interests")
    op.drop_column("users", "partner_personality_type")
    op.drop_column("users", "partner_age_range")
    op.drop_column("users", "partner_name")
    op.drop_column("users", "partner_gender")
    op.drop_column("users", "onboarding_step")
