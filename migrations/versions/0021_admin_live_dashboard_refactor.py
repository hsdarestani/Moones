"""admin live dashboard refactor

Revision ID: 0021_admin_live_dashboard_refactor
Revises: 0020_patch13_natural_proactive
Create Date: 2026-06-25 00:00:00.000000
"""
from alembic import op

revision = "0021_admin_live_dashboard_refactor"
down_revision = "0020_patch13_natural_proactive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_user_id_created_at ON messages (user_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_created_at_admin ON messages (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_id_admin ON messages (id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_role_admin ON messages (role)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_proactive_messages_user_id_sent_at ON proactive_messages (user_id, sent_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_daily_usage_user_id_date_admin ON daily_usage (user_id, date)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_daily_usage_user_id_date_admin")
    op.execute("DROP INDEX IF EXISTS ix_proactive_messages_user_id_sent_at")
    op.execute("DROP INDEX IF EXISTS ix_messages_role_admin")
    op.execute("DROP INDEX IF EXISTS ix_messages_id_admin")
    op.execute("DROP INDEX IF EXISTS ix_messages_created_at_admin")
    op.execute("DROP INDEX IF EXISTS ix_messages_user_id_created_at")
