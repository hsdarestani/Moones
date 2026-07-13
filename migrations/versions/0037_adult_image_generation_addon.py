"""adult image generation addon toggle state

Revision ID: 0037_adult_image_generation_addon
Revises: 0036_low_wallet_message_metadata
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa

revision = '0037_adult_image_generation_addon'
down_revision = '0036_low_wallet_message_metadata'
branch_labels = None
depends_on = None


def _cols(insp, table):
    return {c['name'] for c in insp.get_columns(table)}


def upgrade():
    bind = op.get_bind(); insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if 'user_addons' in tables:
        cols = _cols(insp, 'user_addons')
        if 'is_enabled' not in cols:
            op.add_column('user_addons', sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
        if 'enabled_at' not in cols:
            op.add_column('user_addons', sa.Column('enabled_at', sa.DateTime(), nullable=True))
        if 'disabled_at' not in cols:
            op.add_column('user_addons', sa.Column('disabled_at', sa.DateTime(), nullable=True))
        bind.execute(sa.text("UPDATE user_addons SET is_enabled=true WHERE is_enabled IS NULL"))
    if 'addon_products' in tables:
        cols = _cols(insp, 'addon_products')
        for col in [
            sa.Column('toggleable', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('permanent', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('requires_addon', sa.String(), nullable=True),
            sa.Column('default_enabled_after_purchase', sa.Boolean(), nullable=False, server_default=sa.true()),
        ]:
            if col.name not in cols:
                op.add_column('addon_products', col)
        bind.execute(sa.text("""
            INSERT INTO addon_products (key,title,description,price_toman,price_coins,is_active,sort_order,metadata_json,toggleable,permanent,requires_addon,default_enabled_after_purchase,created_at,updated_at)
            VALUES ('adult_image_generation_unlock','تصاویر بزرگسال مونس','فعال‌سازی اختیاری تصاویر بزرگسال داستانی مجاز؛ افزودنی دریافت عکس مونس هم لازم است.',0,0,true,21,'{}',true,true,'image_generation_unlock',true,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET title=EXCLUDED.title, toggleable=true, permanent=true, requires_addon='image_generation_unlock', default_enabled_after_purchase=true, updated_at=CURRENT_TIMESTAMP
        """))


def downgrade():
    pass
