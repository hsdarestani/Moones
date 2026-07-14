"""image pipeline seed and identity metadata

Revision ID: 0038_image_pipeline_seed_identity
Revises: 0037_adult_image_generation_addon
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa

revision = '0038_image_pipeline_seed_identity'
down_revision = '0037_adult_image_generation_addon'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind(); insp = sa.inspect(bind)
    if 'image_generation_jobs' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('image_generation_jobs')}
        if 'identity_fingerprint' not in cols:
            op.add_column('image_generation_jobs', sa.Column('identity_fingerprint', sa.String(length=64), nullable=True))
            op.create_index('ix_image_generation_jobs_identity_fingerprint', 'image_generation_jobs', ['identity_fingerprint'])


def downgrade():
    bind = op.get_bind(); insp = sa.inspect(bind)
    if 'image_generation_jobs' in insp.get_table_names():
        indexes = {i['name'] for i in insp.get_indexes('image_generation_jobs')}
        if 'ix_image_generation_jobs_identity_fingerprint' in indexes:
            op.drop_index('ix_image_generation_jobs_identity_fingerprint', table_name='image_generation_jobs')
        cols = {c['name'] for c in insp.get_columns('image_generation_jobs')}
        if 'identity_fingerprint' in cols:
            op.drop_column('image_generation_jobs', 'identity_fingerprint')
