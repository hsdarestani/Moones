"""Image request state machine columns

Revision ID: 0040_image_request_state_machine
Revises: 0039_image_pipeline_v2
"""
from alembic import op
import sqlalchemy as sa

revision = '0040_image_request_state_machine'
down_revision = '0039_image_pipeline_v2'
branch_labels = None
depends_on = None

def _cols(insp, table): return {c['name'] for c in insp.get_columns(table)}
def _idxs(insp, table): return {i['name'] for i in insp.get_indexes(table)}
def _add(insp, table, col):
    if col.name not in _cols(insp, table): op.add_column(table, col)
def _idx(insp, name, table, cols):
    if name not in _idxs(insp, table): op.create_index(name, table, cols)

def upgrade():
    bind=op.get_bind(); insp=sa.inspect(bind)
    if 'image_generation_jobs' not in insp.get_table_names(): return
    for col in [
        sa.Column('request_chain_id', sa.String(64), nullable=True),
        sa.Column('current_image_state', sa.String(32), nullable=True),
        sa.Column('parent_request_id', sa.Integer(), nullable=True),
        sa.Column('clarification_target', sa.JSON(), nullable=True),
        sa.Column('resumed_after_topup', sa.Integer(), nullable=True),
        sa.Column('original_user_intent_snapshot', sa.JSON(), nullable=True),
    ]: _add(insp, 'image_generation_jobs', col)
    insp=sa.inspect(bind)
    _idx(insp, 'ix_image_generation_jobs_request_chain_id', 'image_generation_jobs', ['request_chain_id'])
    _idx(insp, 'ix_image_generation_jobs_current_image_state', 'image_generation_jobs', ['current_image_state'])
    _idx(insp, 'ix_image_generation_jobs_parent_request_id', 'image_generation_jobs', ['parent_request_id'])
    bind.execute(sa.text("UPDATE image_generation_jobs SET current_image_state=COALESCE(current_image_state, CASE WHEN status='sent' THEN 'delivered' WHEN status='failed' THEN 'failed' WHEN status='queued' THEN 'queued' ELSE status END) WHERE current_image_state IS NULL"))

def downgrade():
    bind=op.get_bind(); insp=sa.inspect(bind)
    if 'image_generation_jobs' not in insp.get_table_names(): return
    for name in ['ix_image_generation_jobs_parent_request_id','ix_image_generation_jobs_current_image_state','ix_image_generation_jobs_request_chain_id']:
        if name in _idxs(insp, 'image_generation_jobs'): op.drop_index(name, table_name='image_generation_jobs')
    for col in ['original_user_intent_snapshot','resumed_after_topup','clarification_target','parent_request_id','current_image_state','request_chain_id']:
        if col in _cols(insp, 'image_generation_jobs'): op.drop_column('image_generation_jobs', col)
