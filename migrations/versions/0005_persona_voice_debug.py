"""persona voice debug fields

Revision ID: 0005_persona_voice_debug
Revises: 0004_venice_dual_bots_payment_settings_stickers
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_persona_voice_debug"
down_revision = "0004_venice_dual_bots_payment_settings_stickers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("last_processed_response", sa.Text(), nullable=True))
        batch.add_column(sa.Column("last_voice_profile", sa.Text(), nullable=True))
        batch.add_column(sa.Column("last_garbage_filter_triggered", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("last_repetition_filter_triggered", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("last_repetition_filter_triggered")
        batch.drop_column("last_garbage_filter_triggered")
        batch.drop_column("last_voice_profile")
        batch.drop_column("last_processed_response")
