"""llm pipeline debug fields and qwen defaults

Revision ID: 0010_llm_pipeline_debug_qwen_defaults
Revises: 0009_conversation_intelligence_v2_debug
Create Date: 2026-06-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_llm_pipeline_debug_qwen_defaults"
down_revision = "0009_conversation_intelligence_v2_debug"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_raw_llm_response", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_llm_extraction_path", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_llm_retry_used", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("users", "last_llm_retry_used", server_default=None)
    op.execute("UPDATE app_settings SET value='qwen-3-6-plus' WHERE key IN ('llm.venice.model','llm.primary_persian_model')")
    op.execute("INSERT INTO app_settings (key, value, value_type, description) VALUES ('llm.prompt_mode', 'simple_partner_v2', 'string', 'Production prompt mode') ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    op.drop_column("users", "last_llm_retry_used")
    op.drop_column("users", "last_llm_extraction_path")
    op.drop_column("users", "last_raw_llm_response")
