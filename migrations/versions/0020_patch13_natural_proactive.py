"""patch13 natural proactive messages

Revision ID: 0020_patch13_natural_proactive
Revises: 0019_patch12_memory_style_proactive
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_patch13_natural_proactive"
down_revision = "0019_patch12_memory_style_proactive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("proactive_messages")}
    if "intent" not in cols:
        op.add_column("proactive_messages", sa.Column("intent", sa.String(length=64), nullable=True))
    if "metadata" not in cols:
        op.add_column("proactive_messages", sa.Column("metadata", sa.JSON(), nullable=True))
    op.execute("UPDATE app_settings SET value='2' WHERE key='proactive.daily_max_per_user' AND value='1'")
    for key, value, desc in [
        ("proactive.send_window_start", "10:30", "Proactive allowed send window start (server/local time)"),
        ("proactive.send_window_end", "23:30", "Proactive allowed send window end (server/local time)"),
    ]:
        op.execute(sa.text("INSERT INTO app_settings (key,value,value_type,description) SELECT :k,:v,'string',:d WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE key=:k)").bindparams(k=key, v=value, d=desc))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("proactive_messages")}
    if "metadata" in cols:
        op.drop_column("proactive_messages", "metadata")
    if "intent" in cols:
        op.drop_column("proactive_messages", "intent")
