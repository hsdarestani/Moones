"""paid add-ons

Revision ID: 0025_paid_addons
Revises: 0024_paid_media_inputs
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0025_paid_addons"
down_revision = "0024_paid_media_inputs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("addon_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_toman", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_addon_products_key", "addon_products", ["key"])
    op.create_table("user_addons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("addon_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("source", sa.String(), nullable=False, server_default="manual_payment"),
        sa.Column("payment_receipt_id", sa.Integer(), nullable=True),
        sa.Column("price_paid_toman", sa.Integer(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "addon_key", name="uq_user_addons_user_addon"),
    )
    op.create_index("ix_user_addons_user_id", "user_addons", ["user_id"])
    op.create_index("ix_user_addons_addon_key", "user_addons", ["addon_key"])
    op.create_index("ix_user_addons_status", "user_addons", ["status"])
    op.add_column("users", sa.Column("intimacy_level", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("intimacy_override_max", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("mature_intimacy_unlocked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("mature_intimacy_unlocked_at", sa.DateTime(), nullable=True))
    op.add_column("payment_receipts", sa.Column("purpose", sa.String(length=32), nullable=False, server_default="wallet_topup"))
    op.add_column("payment_receipts", sa.Column("addon_key", sa.String(length=64), nullable=True))
    op.execute("""
    INSERT INTO addon_products (key,title,description,price_toman,is_active,sort_order)
    VALUES ('intimacy_max_unlock','افزایش صمیمیت رابطه','صمیمیت رابطه‌ات با مونس را به بالاترین سطح می‌رساند، بدون تغییر پلن.',100000,true,10)
    ON CONFLICT (key) DO NOTHING
    """)
    for key, val, typ in [("addon_intimacy_max_price_toman","100000","integer"),("addon_intimacy_max_enabled","true","boolean"),("addon_intimacy_max_title","افزایش صمیمیت رابطه","string")]:
        op.execute(f"INSERT INTO app_settings (key,value,value_type,description) VALUES ('{key}','{val}','{typ}','Add-on setting') ON CONFLICT (key) DO NOTHING")


def downgrade():
    op.drop_column("payment_receipts", "addon_key"); op.drop_column("payment_receipts", "purpose")
    op.drop_column("users", "mature_intimacy_unlocked_at"); op.drop_column("users", "mature_intimacy_unlocked"); op.drop_column("users", "intimacy_override_max"); op.drop_column("users", "intimacy_level")
    op.drop_table("user_addons"); op.drop_table("addon_products")
