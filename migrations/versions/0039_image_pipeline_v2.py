"""Image Pipeline v2 structured plan fields

Revision ID: 0039_image_pipeline_v2
Revises: 0038_image_pipeline_seed_identity
"""
from alembic import op
import sqlalchemy as sa

revision = '0039_image_pipeline_v2'
down_revision = '0038_image_pipeline_seed_identity'
branch_labels = None
depends_on = None

def _add_col(insp, table, col):
    if col.name not in {c['name'] for c in insp.get_columns(table)}:
        op.add_column(table, col)

def _idx(insp, name, table, cols):
    if name not in {i['name'] for i in insp.get_indexes(table)}:
        op.create_index(name, table, cols)

def upgrade():
    bind=op.get_bind(); insp=sa.inspect(bind)
    if 'image_generation_jobs' not in insp.get_table_names(): return
    for col in [
        sa.Column('plan_version', sa.String(64), nullable=True),
        sa.Column('resolved_plan_json', sa.JSON(), nullable=True),
        sa.Column('source_image_job_id', sa.Integer(), nullable=True),
        sa.Column('image_action', sa.String(32), nullable=True),
        sa.Column('identity_seed', sa.Integer(), nullable=True),
        sa.Column('variation_index', sa.Integer(), nullable=True),
        sa.Column('final_provider_seed', sa.Integer(), nullable=True),
        sa.Column('policy_reason_code', sa.String(128), nullable=True),
        sa.Column('inbound_message_id', sa.Integer(), nullable=True),
        sa.Column('assistant_ack_message_id', sa.Integer(), nullable=True),
        sa.Column('delivery_message_id', sa.Integer(), nullable=True),
    ]: _add_col(insp, 'image_generation_jobs', col)
    insp=sa.inspect(bind)
    _idx(insp,'ix_image_generation_jobs_user_chat_status_sent','image_generation_jobs',['user_id','chat_id','status','sent_at'])
    _idx(insp,'ix_image_generation_jobs_source_image_job_id','image_generation_jobs',['source_image_job_id'])
    _idx(insp,'ix_image_generation_jobs_image_action','image_generation_jobs',['image_action'])
    _idx(insp,'ix_image_generation_jobs_plan_version','image_generation_jobs',['plan_version'])
    if 'ix_image_generation_jobs_identity_fingerprint' not in {i['name'] for i in insp.get_indexes('image_generation_jobs')}:
        _idx(insp,'ix_image_generation_jobs_identity_fingerprint','image_generation_jobs',['identity_fingerprint'])
    bind.execute(sa.text("UPDATE image_generation_jobs SET plan_version=COALESCE(plan_version, 'legacy-partial'), image_action=COALESCE(image_action, 'new_generation'), final_provider_seed=COALESCE(final_provider_seed, seed) WHERE plan_version IS NULL OR image_action IS NULL OR final_provider_seed IS NULL"))

def downgrade():
    bind=op.get_bind(); insp=sa.inspect(bind)
    if 'image_generation_jobs' not in insp.get_table_names(): return
    for name in ['ix_image_generation_jobs_plan_version','ix_image_generation_jobs_image_action','ix_image_generation_jobs_source_image_job_id','ix_image_generation_jobs_user_chat_status_sent']:
        if name in {i['name'] for i in insp.get_indexes('image_generation_jobs')}: op.drop_index(name, table_name='image_generation_jobs')
    for col in ['delivery_message_id','assistant_ack_message_id','inbound_message_id','policy_reason_code','final_provider_seed','variation_index','identity_seed','image_action','source_image_job_id','resolved_plan_json','plan_version']:
        if col in {c['name'] for c in insp.get_columns('image_generation_jobs')}: op.drop_column('image_generation_jobs', col)
