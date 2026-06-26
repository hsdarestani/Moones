"""partner life and persian audit indexes

Revision ID: 0022_partner_life_persian_audit
Revises: 0021_admin_live_dashboard_refactor
Create Date: 2026-06-26
"""
from alembic import op

revision = "0022_partner_life_persian_audit"
down_revision = "0021_admin_live_dashboard_refactor"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute('''
    CREATE TABLE IF NOT EXISTS partner_life_events (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        event_date DATE NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        title VARCHAR(180) NOT NULL,
        content TEXT NOT NULL,
        mood VARCHAR(64),
        growth_note TEXT,
        source VARCHAR(32) NOT NULL DEFAULT 'deterministic',
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_partner_life_user_date UNIQUE (user_id, event_date)
    )
    ''')
    op.execute('CREATE INDEX IF NOT EXISTS ix_partner_life_events_user_id_event_date ON partner_life_events (user_id, event_date)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_partner_life_events_created_at ON partner_life_events (created_at)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_partner_life_events_event_type ON partner_life_events (event_type)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_bot_style_audits_issue_created ON bot_style_audits (issue_type, created_at)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_bot_style_audits_user_created ON bot_style_audits (user_id, created_at)')

def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_bot_style_audits_user_created')
    op.execute('DROP INDEX IF EXISTS ix_bot_style_audits_issue_created')
    op.execute('DROP INDEX IF EXISTS ix_partner_life_events_event_type')
    op.execute('DROP INDEX IF EXISTS ix_partner_life_events_created_at')
    op.execute('DROP INDEX IF EXISTS ix_partner_life_events_user_id_event_date')
    op.execute('DROP TABLE IF EXISTS partner_life_events')
