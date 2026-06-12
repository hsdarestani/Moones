"""wallet subscription usage

Revision ID: 0003_wallet_subscription_usage
Revises: 0002_onboarding_admin_llm_fields
Create Date: 2026-06-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_wallet_subscription_usage"
down_revision = "0002_onboarding_admin_llm_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("balance_coins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_added_coins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_spent_coins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_wallets_id"), "wallets", ["id"], unique=False)
    op.create_index(op.f("ix_wallets_user_id"), "wallets", ["user_id"], unique=False)

    op.create_table(
        "wallet_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("wallet_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("amount_coins", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_wallet_transactions_id"), "wallet_transactions", ["id"], unique=False)
    op.create_index(op.f("ix_wallet_transactions_user_id"), "wallet_transactions", ["user_id"], unique=False)
    op.create_index(op.f("ix_wallet_transactions_wallet_id"), "wallet_transactions", ["wallet_id"], unique=False)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False, server_default="free"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("starts_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_subscriptions_id"), "subscriptions", ["id"], unique=False)
    op.create_index(op.f("ix_subscriptions_user_id"), "subscriptions", ["user_id"], unique=False)
    op.create_index("ix_subscriptions_user_active", "subscriptions", ["user_id", "status"], unique=False)
    op.create_index("uq_subscriptions_one_active_user", "subscriptions", ["user_id"], unique=True, postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))

    op.create_table(
        "daily_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("messages_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date", name="uq_daily_usage_user_date"),
    )
    op.create_index(op.f("ix_daily_usage_date"), "daily_usage", ["date"], unique=False)
    op.create_index(op.f("ix_daily_usage_id"), "daily_usage", ["id"], unique=False)
    op.create_index(op.f("ix_daily_usage_user_id"), "daily_usage", ["user_id"], unique=False)

    op.execute("""
        INSERT INTO wallets (user_id, balance_coins, total_added_coins, total_spent_coins, created_at, updated_at)
        SELECT users.id, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM users
        WHERE NOT EXISTS (SELECT 1 FROM wallets WHERE wallets.user_id = users.id)
    """)
    op.execute("""
        INSERT INTO subscriptions (user_id, plan, status, starts_at, created_at, updated_at)
        SELECT users.id, 'free', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM users
        WHERE NOT EXISTS (SELECT 1 FROM subscriptions WHERE subscriptions.user_id = users.id AND subscriptions.status = 'active')
    """)


def downgrade() -> None:
    op.drop_index(op.f("ix_daily_usage_user_id"), table_name="daily_usage")
    op.drop_index(op.f("ix_daily_usage_id"), table_name="daily_usage")
    op.drop_index(op.f("ix_daily_usage_date"), table_name="daily_usage")
    op.drop_table("daily_usage")
    op.drop_index("uq_subscriptions_one_active_user", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_active", table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_user_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_wallet_transactions_wallet_id"), table_name="wallet_transactions")
    op.drop_index(op.f("ix_wallet_transactions_user_id"), table_name="wallet_transactions")
    op.drop_index(op.f("ix_wallet_transactions_id"), table_name="wallet_transactions")
    op.drop_table("wallet_transactions")
    op.drop_index(op.f("ix_wallets_user_id"), table_name="wallets")
    op.drop_index(op.f("ix_wallets_id"), table_name="wallets")
    op.drop_table("wallets")
