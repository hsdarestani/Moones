"""token plans voice stickers

Revision ID: 0012_token_plans_voice_stickers
Revises: 0011_mood_delivery
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_token_plans_voice_stickers"
down_revision = "0011_mood_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("daily_usage") as batch:
        batch.add_column(sa.Column("voice_tokens", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("daily_voice_sent", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("sticker_items") as batch:
        batch.add_column(sa.Column("persona_gender", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("persona_style", sa.String(length=64), nullable=True))
    settings = sa.table("app_settings", sa.column("key", sa.String), sa.column("value", sa.Text), sa.column("value_type", sa.String), sa.column("description", sa.Text))
    op.bulk_insert(settings, [
        {"key":"subscription.mini.price_coins","value":"5900","value_type":"integer","description":"Mini plan price"},
        {"key":"subscription.basic.price_coins","value":"9900","value_type":"integer","description":"Basic plan price"},
        {"key":"subscription.plus.price_coins","value":"22900","value_type":"integer","description":"Plus plan price"},
        {"key":"subscription.vip.price_coins","value":"49000","value_type":"integer","description":"VIP plan price"},
        {"key":"limits.free.daily_token_limit","value":"20000","value_type":"integer","description":"Free daily usage capacity"},
        {"key":"limits.mini.daily_token_limit","value":"80000","value_type":"integer","description":"Mini daily usage capacity"},
        {"key":"limits.basic.daily_token_limit","value":"150000","value_type":"integer","description":"Basic daily usage capacity"},
        {"key":"limits.plus.daily_token_limit","value":"500000","value_type":"integer","description":"Plus daily usage capacity"},
        {"key":"limits.vip.daily_token_limit","value":"1200000","value_type":"integer","description":"VIP daily usage capacity"},
    ])


def downgrade() -> None:
    op.execute("DELETE FROM app_settings WHERE key IN ('subscription.mini.price_coins','subscription.basic.price_coins','subscription.plus.price_coins','subscription.vip.price_coins','limits.free.daily_token_limit','limits.mini.daily_token_limit','limits.basic.daily_token_limit','limits.plus.daily_token_limit','limits.vip.daily_token_limit')")
    with op.batch_alter_table("sticker_items") as batch:
        batch.drop_column("persona_style")
        batch.drop_column("persona_gender")
    with op.batch_alter_table("daily_usage") as batch:
        batch.drop_column("daily_voice_sent")
        batch.drop_column("voice_tokens")
