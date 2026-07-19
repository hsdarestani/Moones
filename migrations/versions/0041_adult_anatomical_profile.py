"""Add explicit fictional adult anatomical profile

Revision ID: 0041_adult_anatomical_profile
Revises: 0040_image_request_state_machine
"""
from alembic import op
import sqlalchemy as sa

revision = '0041_adult_anatomical_profile'
down_revision = '0040_image_request_state_machine'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind(); insp = sa.inspect(bind)
    if 'partner_visual_profiles' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('partner_visual_profiles')}
    if 'anatomical_profile' not in cols:
        op.add_column('partner_visual_profiles', sa.Column('anatomical_profile', sa.String(32), nullable=True))
    # Nullable, non-blind migration: only copies a previously explicit structured value.
    # Existing rows without profile_json.anatomical_profile remain NULL/unspecified.
    dialect = bind.dialect.name
    if dialect == 'postgresql':
        bind.execute(sa.text("""
            UPDATE partner_visual_profiles
            SET anatomical_profile = lower(profile_json->>'anatomical_profile')
            WHERE anatomical_profile IS NULL
              AND lower(profile_json->>'anatomical_profile') IN ('male','female','intersex','unspecified')
        """))


def downgrade():
    bind = op.get_bind(); insp = sa.inspect(bind)
    if 'partner_visual_profiles' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('partner_visual_profiles')}
    if 'anatomical_profile' in cols:
        op.drop_column('partner_visual_profiles', 'anatomical_profile')
