"""human presence delivery jobs

Revision ID: 0023_human_presence_engine
Revises: 0022_partner_life_persian_audit
Create Date: 2026-06-27
"""
from alembic import op

revision = "0023_human_presence_engine"
down_revision = "0022_partner_life_persian_audit"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute('''
    CREATE TABLE IF NOT EXISTS human_delivery_jobs (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        telegram_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        job_type VARCHAR(32) NOT NULL,
        text TEXT NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        source_message_id INTEGER NULL,
        source_created_at TIMESTAMP WITHOUT TIME ZONE NULL,
        scheduled_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        sent_at TIMESTAMP WITHOUT TIME ZONE NULL,
        cancelled_at TIMESTAMP WITHOUT TIME ZONE NULL,
        expires_at TIMESTAMP WITHOUT TIME ZONE NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
    )''')
    op.execute('CREATE INDEX IF NOT EXISTS ix_human_delivery_jobs_status_scheduled_at ON human_delivery_jobs (status, scheduled_at)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_human_delivery_jobs_user_created_at ON human_delivery_jobs (user_id, created_at)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_human_delivery_jobs_expires_at ON human_delivery_jobs (expires_at)')

def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_human_delivery_jobs_expires_at')
    op.execute('DROP INDEX IF EXISTS ix_human_delivery_jobs_user_created_at')
    op.execute('DROP INDEX IF EXISTS ix_human_delivery_jobs_status_scheduled_at')
    op.execute('DROP TABLE IF EXISTS human_delivery_jobs')
