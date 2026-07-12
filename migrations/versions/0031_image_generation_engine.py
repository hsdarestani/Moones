"""image generation engine

Revision ID: 0031_image_generation_engine
Revises: 0030_coin_usage_billing
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa

revision='0031_image_generation_engine'
down_revision='0030_coin_usage_billing'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind(); insp=sa.inspect(bind)
    cols={c['name'] for c in insp.get_columns('users')}
    if 'adult_content_confirmed' not in cols: op.add_column('users', sa.Column('adult_content_confirmed', sa.Boolean(), nullable=False, server_default=sa.false()))
    if 'adult_content_confirmed_at' not in cols: op.add_column('users', sa.Column('adult_content_confirmed_at', sa.DateTime(), nullable=True))
    if 'adult_content_confirmation_version' not in cols: op.add_column('users', sa.Column('adult_content_confirmation_version', sa.String(32), nullable=True))
    if 'partner_visual_profiles' not in insp.get_table_names():
        op.create_table('partner_visual_profiles', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False), sa.Column('version',sa.Integer(),nullable=False,server_default='1'), sa.Column('partner_name',sa.String(64)), sa.Column('fictional_age',sa.Integer(),nullable=False,server_default='24'), sa.Column('gender_presentation',sa.String(64)), sa.Column('ethnicity_or_regional_style',sa.Text()), sa.Column('face_description',sa.Text()), sa.Column('hair_description',sa.Text()), sa.Column('eye_description',sa.Text()), sa.Column('skin_description',sa.Text()), sa.Column('body_description',sa.Text()), sa.Column('height_impression',sa.Text()), sa.Column('default_style',sa.Text()), sa.Column('distinguishing_details',sa.Text()), sa.Column('default_city',sa.String(64)), sa.Column('base_seed',sa.Integer(),nullable=False,server_default='-1'), sa.Column('profile_json',sa.JSON()), sa.Column('source',sa.String(32),nullable=False,server_default='derived'), sa.Column('created_at',sa.DateTime(),nullable=False), sa.Column('updated_at',sa.DateTime(),nullable=False), sa.UniqueConstraint('user_id'))
        op.create_index('ix_partner_visual_profiles_user_id','partner_visual_profiles',['user_id'])
    if 'image_generation_jobs' not in insp.get_table_names():
        op.create_table('image_generation_jobs', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('idempotency_key',sa.String(255),nullable=False), sa.Column('correlation_id',sa.String(255),nullable=False), sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False), sa.Column('chat_id',sa.BigInteger(),nullable=False), sa.Column('source_telegram_message_id',sa.BigInteger()), sa.Column('addon_key',sa.String(64),nullable=False,server_default='image_generation_unlock'), sa.Column('status',sa.String(32),nullable=False,server_default='queued'), sa.Column('content_mode',sa.String(32),nullable=False,server_default='normal'), sa.Column('user_request',sa.Text()), sa.Column('prompt',sa.Text()), sa.Column('negative_prompt',sa.Text()), sa.Column('prompt_engine_version',sa.String(64)), sa.Column('visual_profile_version',sa.Integer()), sa.Column('provider',sa.String(32),nullable=False,server_default='venice'), sa.Column('model',sa.String(128),nullable=False,server_default='krea-2-turbo'), sa.Column('width',sa.Integer(),nullable=False,server_default='1024'), sa.Column('height',sa.Integer(),nullable=False,server_default='1280'), sa.Column('steps',sa.Integer(),nullable=False,server_default='45'), sa.Column('cfg_scale',sa.Integer(),nullable=False,server_default='4'), sa.Column('seed',sa.Integer(),nullable=False,server_default='-1'), sa.Column('attempt_count',sa.Integer(),nullable=False,server_default='0'), sa.Column('max_attempts',sa.Integer(),nullable=False,server_default='3'), sa.Column('scheduled_at',sa.DateTime(),nullable=False), sa.Column('locked_at',sa.DateTime()), sa.Column('lock_expires_at',sa.DateTime()), sa.Column('started_at',sa.DateTime()), sa.Column('generated_at',sa.DateTime()), sa.Column('sent_at',sa.DateTime()), sa.Column('failed_at',sa.DateTime()), sa.Column('provider_request_id',sa.String(255)), sa.Column('usage_charge_id',sa.Integer(),sa.ForeignKey('usage_charges.id')), sa.Column('telegram_message_id',sa.BigInteger()), sa.Column('error_code',sa.String(64)), sa.Column('error_message',sa.Text()), sa.Column('metadata_json',sa.JSON()), sa.Column('created_at',sa.DateTime(),nullable=False), sa.Column('updated_at',sa.DateTime(),nullable=False), sa.UniqueConstraint('idempotency_key'))
        for n in ['idempotency_key','correlation_id','user_id','status','lock_expires_at','usage_charge_id']: op.create_index(f'ix_image_generation_jobs_{n}','image_generation_jobs',[n])
    if 'image_generation_artifacts' not in insp.get_table_names():
        op.create_table('image_generation_artifacts', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('job_id',sa.Integer(),sa.ForeignKey('image_generation_jobs.id'),nullable=False), sa.Column('mime_type',sa.String(64),nullable=False), sa.Column('checksum',sa.String(128),nullable=False), sa.Column('byte_size',sa.Integer(),nullable=False), sa.Column('image_bytes',sa.LargeBinary()), sa.Column('created_at',sa.DateTime(),nullable=False), sa.Column('cleared_at',sa.DateTime()), sa.UniqueConstraint('job_id'))
        op.create_index('ix_image_generation_artifacts_job_id','image_generation_artifacts',['job_id'])
    if 'image_generation_feedback' not in insp.get_table_names():
        op.create_table('image_generation_feedback', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('job_id',sa.Integer(),sa.ForeignKey('image_generation_jobs.id'),nullable=False), sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False), sa.Column('rating',sa.String(16),nullable=False), sa.Column('created_at',sa.DateTime(),nullable=False), sa.UniqueConstraint('job_id','user_id',name='uq_image_feedback_job_user'))
        op.create_index('ix_image_generation_feedback_job_id','image_generation_feedback',['job_id']); op.create_index('ix_image_generation_feedback_user_id','image_generation_feedback',['user_id'])
    bind.execute(sa.text("INSERT INTO addon_products (key,title,description,price_toman,price_coins,is_active,sort_order,metadata_json,created_at,updated_at) VALUES ('image_generation_unlock','ساخت تصویر مونس','باز کردن درخواست تصویر از مونس؛ هر تصویر هزینه مصرف جداگانه دارد.',0,500,true,20,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) ON CONFLICT (key) DO UPDATE SET title=EXCLUDED.title, price_coins=COALESCE(NULLIF(addon_products.price_coins,0),500), updated_at=CURRENT_TIMESTAMP"))

def downgrade():
    for t in ['image_generation_feedback','image_generation_artifacts','image_generation_jobs','partner_visual_profiles']:
        op.drop_table(t)
    for c in ['adult_content_confirmation_version','adult_content_confirmed_at','adult_content_confirmed']:
        try: op.drop_column('users', c)
        except Exception: pass
