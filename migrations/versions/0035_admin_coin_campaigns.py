"""admin coin campaigns

Revision ID: 0035_admin_coin_campaigns
Revises: 0034_admin_security_audit
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_admin_coin_campaigns"
down_revision = "0034_admin_security_audit"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("admin_coin_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_key", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("admin_note", sa.Text(), nullable=False),
        sa.Column("amount_coins", sa.BigInteger(), nullable=False),
        sa.Column("audience_type", sa.String(length=64), nullable=False, server_default="all_users"),
        sa.Column("audience_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("previewed_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("target_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credited_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_credited_coins", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"]),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("campaign_key"))
    op.create_index(op.f("ix_admin_coin_campaigns_id"), "admin_coin_campaigns", ["id"], unique=False)
    op.create_index(op.f("ix_admin_coin_campaigns_campaign_key"), "admin_coin_campaigns", ["campaign_key"], unique=False)
    op.create_index(op.f("ix_admin_coin_campaigns_status"), "admin_coin_campaigns", ["status"], unique=False)
    op.create_table("admin_coin_campaign_recipients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("wallet_transaction_id", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credited_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["admin_coin_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_transaction_id"], ["wallet_transactions.id"]),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("campaign_id", "user_id", name="uq_admin_coin_campaign_recipient"))
    for col in ["id","campaign_id","user_id","status","wallet_transaction_id"]:
        op.create_index(op.f(f"ix_admin_coin_campaign_recipients_{col}"), "admin_coin_campaign_recipients", [col], unique=False)

def downgrade():
    op.drop_table("admin_coin_campaign_recipients")
    op.drop_table("admin_coin_campaigns")
