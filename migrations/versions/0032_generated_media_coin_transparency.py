"""generated media coin transparency

Revision ID: 0032_generated_media_coin_transparency
Revises: 0031_image_generation_engine
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa

revision='0032_generated_media_coin_transparency'
down_revision='0031_image_generation_engine'
branch_labels=None
depends_on=None

SETTINGS={
 'subscriptions.new_sales_enabled':('false','boolean','Enable new experience membership sales'),
 'subscription.mini.price_coins':('5900','integer','Mini membership price in coins'),
 'subscription.basic.price_coins':('9900','integer','Basic membership price in coins'),
 'subscription.plus.price_coins':('22900','integer','Plus membership price in coins'),
 'subscription.vip.price_coins':('49000','integer','VIP membership price in coins'),
 'generated_media.forward_enabled':('false','boolean','Forward generated media to archive group'),
 'generated_media.chat_id':('','string','Generated media archive chat id'),
 'generated_media.forward_images':('true','boolean','Forward generated images'),
 'generated_media.forward_voices':('true','boolean','Forward generated voices'),
 'generated_media.fallback_to_support_media_chat_id':('false','boolean','Fallback to support media chat id'),
 'image_generation.adult_enabled':('false','boolean','Allow adult image generation'),
 'image_generation.soft_safety_enabled':('true','boolean','Enable optional soft safety'),
}

def _add_col(insp, table, col):
    if col.name not in {c['name'] for c in insp.get_columns(table)}: op.add_column(table, col)

def upgrade():
    bind=op.get_bind(); insp=sa.inspect(bind); tables=insp.get_table_names()
    if 'payment_receipts' in tables:
        _add_col(insp,'payment_receipts',sa.Column('paid_toman',sa.Integer(),nullable=True))
    if 'image_generation_jobs' in tables:
        for col in [sa.Column('archive_status',sa.String(32),nullable=False,server_default='pending'),sa.Column('archive_telegram_message_id',sa.BigInteger()),sa.Column('archive_error',sa.Text()),sa.Column('archive_sent_at',sa.DateTime()),sa.Column('thumbnail_bytes',sa.LargeBinary()),sa.Column('thumbnail_mime_type',sa.String(64))]: _add_col(insp,'image_generation_jobs',col)
    if 'generated_voice_outputs' not in tables:
        op.create_table('generated_voice_outputs',
          sa.Column('id',sa.Integer(),primary_key=True),sa.Column('idempotency_key',sa.String(255),nullable=False),sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('chat_id',sa.BigInteger(),nullable=False),sa.Column('source_telegram_message_id',sa.BigInteger()),sa.Column('usage_charge_id',sa.Integer(),sa.ForeignKey('usage_charges.id')),sa.Column('text_spoken',sa.Text()),sa.Column('voice_name',sa.String(128)),sa.Column('provider',sa.String(64)),sa.Column('model',sa.String(128)),sa.Column('mime_type',sa.String(64)),sa.Column('byte_size',sa.Integer()),sa.Column('checksum',sa.String(128)),sa.Column('audio_bytes',sa.LargeBinary()),sa.Column('status',sa.String(32),nullable=False,server_default='pending'),sa.Column('archive_status',sa.String(32),nullable=False,server_default='pending'),sa.Column('attempt_count',sa.Integer(),nullable=False,server_default='0'),sa.Column('user_telegram_message_id',sa.BigInteger()),sa.Column('archive_telegram_message_id',sa.BigInteger()),sa.Column('feedback',sa.String(16)),sa.Column('generated_at',sa.DateTime()),sa.Column('sent_at',sa.DateTime()),sa.Column('archive_sent_at',sa.DateTime()),sa.Column('error_code',sa.String(64)),sa.Column('error_message',sa.Text()),sa.Column('archive_error',sa.Text()),sa.Column('metadata_json',sa.JSON()),sa.Column('created_at',sa.DateTime(),nullable=False),sa.Column('updated_at',sa.DateTime(),nullable=False),sa.UniqueConstraint('idempotency_key',name='uq_generated_voice_idempotency'))
        for c in ['idempotency_key','user_id','usage_charge_id','status','archive_status']: op.create_index(f'ix_generated_voice_outputs_{c}','generated_voice_outputs',[c])
    force_keys={'subscription.mini.price_coins','subscription.basic.price_coins','subscription.plus.price_coins','subscription.vip.price_coins','subscriptions.new_sales_enabled'}
    for k,(v,t,d) in SETTINGS.items():
        bind.execute(sa.text("INSERT INTO app_settings (key,value,value_type,description,created_at,updated_at) VALUES (:k,:v,:t,:d,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) ON CONFLICT (key) DO NOTHING"),dict(k=k,v=v,t=t,d=d))
        if k in force_keys:
            bind.execute(sa.text("UPDATE app_settings SET value=:v,value_type=:t,description=:d,updated_at=CURRENT_TIMESTAMP WHERE key=:k"),dict(k=k,v=v,t=t,d=d))

def downgrade():
    op.drop_table('generated_voice_outputs')
