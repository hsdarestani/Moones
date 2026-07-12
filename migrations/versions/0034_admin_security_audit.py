"""admin security audit

Revision ID: 0034_admin_security_audit
Revises: 0033_wallet_ux_proactive_defaults
Create Date: 2026-07-12
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0034_admin_security_audit"
down_revision: Union[str, None] = "0033_wallet_ux_proactive_defaults"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table("admin_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admin_users_username", "admin_users", ["username"], unique=True)
    op.create_index("ix_admin_users_role", "admin_users", ["role"])
    op.create_table("admin_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_user_id", sa.Integer(), sa.ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("user_agent_summary", sa.String(length=255), nullable=True),
        sa.Column("ip_prefix", sa.String(length=64), nullable=True),
        sa.Column("csrf_token_hash", sa.String(length=128), nullable=True),
    )
    op.create_index("ix_admin_sessions_admin_user_id", "admin_sessions", ["admin_user_id"])
    op.create_index("ix_admin_sessions_token_hash", "admin_sessions", ["token_hash"], unique=True)
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])
    op.create_index("ix_admin_sessions_revoked_at", "admin_sessions", ["revoked_at"])
    op.create_table("admin_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_user_id", sa.Integer(), sa.ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admin_audit_events_admin_user_id", "admin_audit_events", ["admin_user_id"])
    op.create_index("ix_admin_audit_events_action", "admin_audit_events", ["action"])
    op.create_index("ix_admin_audit_events_created_at", "admin_audit_events", ["created_at"])
    op.create_index("ix_admin_audit_events_request_id", "admin_audit_events", ["request_id"])
    op.create_index("ix_admin_audit_target", "admin_audit_events", ["target_type", "target_id"])
    op.create_index("ix_admin_audit_action_created", "admin_audit_events", ["action", "created_at"])

def downgrade() -> None:
    op.drop_table("admin_audit_events")
    op.drop_table("admin_sessions")
    op.drop_table("admin_users")
