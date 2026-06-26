"""partner life and persian audit indexes

Revision ID: 0022_partner_life_persian_audit
Revises: 0021_admin_live_dashboard_refactor
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_partner_life_persian_audit"
down_revision = "0021_admin_live_dashboard_refactor"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "partner_life_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("mood", sa.String(length=64), nullable=True),
        sa.Column("growth_note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="deterministic"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "event_date", name="uq_partner_life_user_date"),
    )
    op.create_index("ix_partner_life_events_user_id_event_date", "partner_life_events", ["user_id", "event_date"])
    op.create_index("ix_partner_life_events_created_at", "partner_life_events", ["created_at"])
    op.create_index("ix_partner_life_events_event_type", "partner_life_events", ["event_type"])
    op.create_index("ix_bot_style_audits_issue_created", "bot_style_audits", ["issue_type", "created_at"])
    op.create_index("ix_bot_style_audits_user_created", "bot_style_audits", ["user_id", "created_at"])

def downgrade() -> None:
    op.drop_index("ix_bot_style_audits_user_created", table_name="bot_style_audits")
    op.drop_index("ix_bot_style_audits_issue_created", table_name="bot_style_audits")
    op.drop_index("ix_partner_life_events_event_type", table_name="partner_life_events")
    op.drop_index("ix_partner_life_events_created_at", table_name="partner_life_events")
    op.drop_index("ix_partner_life_events_user_id_event_date", table_name="partner_life_events")
    op.drop_table("partner_life_events")
