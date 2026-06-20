"""admin analytics events

Revision ID: 0018_admin_analytics_events
Revises: 0017_patch9_soft_upsell
Create Date: 2026-06-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_admin_analytics_events"
down_revision = "0017_patch9_soft_upsell"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_date", sa.DateTime(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analytics_events_id"), "analytics_events", ["id"], unique=False)
    op.create_index(op.f("ix_analytics_events_user_id"), "analytics_events", ["user_id"], unique=False)
    op.create_index(op.f("ix_analytics_events_event_type"), "analytics_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_analytics_events_event_date"), "analytics_events", ["event_date"], unique=False)
    op.create_index("ix_analytics_events_type_date", "analytics_events", ["event_type", "event_date"], unique=False)
    op.create_index("ix_analytics_events_user_date", "analytics_events", ["user_id", "event_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_analytics_events_user_date", table_name="analytics_events")
    op.drop_index("ix_analytics_events_type_date", table_name="analytics_events")
    op.drop_index(op.f("ix_analytics_events_event_date"), table_name="analytics_events")
    op.drop_index(op.f("ix_analytics_events_event_type"), table_name="analytics_events")
    op.drop_index(op.f("ix_analytics_events_user_id"), table_name="analytics_events")
    op.drop_index(op.f("ix_analytics_events_id"), table_name="analytics_events")
    op.drop_table("analytics_events")
