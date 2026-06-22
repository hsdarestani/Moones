"""patch12 memory style proactive

Revision ID: 0019_patch12_memory_style_proactive
Revises: 0018_admin_analytics_events
"""
from alembic import op
import sqlalchemy as sa
revision = "0019_patch12_memory_style_proactive"
down_revision = "0018_admin_analytics_events"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("bot_style_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("audit_date", sa.Date(), nullable=False),
        sa.Column("issue_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("original_excerpt", sa.Text(), nullable=False),
        sa.Column("suggested_rewrite", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("applied_to_rules", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["user_id"],["users.id"]), sa.ForeignKeyConstraint(["message_id"],["messages.id"]), sa.PrimaryKeyConstraint("id"))
    op.create_index(op.f("ix_bot_style_audits_id"), "bot_style_audits", ["id"])
    op.create_index(op.f("ix_bot_style_audits_user_id"), "bot_style_audits", ["user_id"])
    op.create_index(op.f("ix_bot_style_audits_message_id"), "bot_style_audits", ["message_id"])
    op.create_index(op.f("ix_bot_style_audits_audit_date"), "bot_style_audits", ["audit_date"])
    op.create_index(op.f("ix_bot_style_audits_issue_type"), "bot_style_audits", ["issue_type"])
    op.execute("UPDATE users SET proactive_messages_enabled = 1 WHERE proactive_messages_enabled IS NULL")
    op.execute("UPDATE app_settings SET value='true' WHERE key='proactive.enabled' AND value IN ('false','False','0')")
    op.execute("UPDATE app_settings SET value='vip,plus,basic,mini,free,daily,free_daily,none,trial' WHERE key='proactive.allowed_plans'")

def downgrade():
    op.drop_index(op.f("ix_bot_style_audits_issue_type"), table_name="bot_style_audits")
    op.drop_index(op.f("ix_bot_style_audits_audit_date"), table_name="bot_style_audits")
    op.drop_index(op.f("ix_bot_style_audits_message_id"), table_name="bot_style_audits")
    op.drop_index(op.f("ix_bot_style_audits_user_id"), table_name="bot_style_audits")
    op.drop_index(op.f("ix_bot_style_audits_id"), table_name="bot_style_audits")
    op.drop_table("bot_style_audits")
