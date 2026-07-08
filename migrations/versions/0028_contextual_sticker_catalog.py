"""contextual sticker catalog

Revision ID: 0028_contextual_sticker_catalog
Revises: 0027_admin_dashboard_v2_usage, 0022_voice_and_proactive_metadata
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_contextual_sticker_catalog"
down_revision = ("0027_admin_dashboard_v2_usage", "0022_voice_and_proactive_metadata")
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    return {col["name"] for col in sa.inspect(bind).get_columns(table_name)}


def _add_if_missing(batch, existing: set[str], name: str, column) -> None:
    if name not in existing:
        batch.add_column(column)


def upgrade() -> None:
    existing = _columns("sticker_items")
    with op.batch_alter_table("sticker_items") as batch:
        _add_if_missing(batch, existing, "key", sa.Column("key", sa.String(length=128), nullable=True))
        _add_if_missing(batch, existing, "category", sa.Column("category", sa.String(length=32), nullable=False, server_default="normal"))
        _add_if_missing(batch, existing, "meaning", sa.Column("meaning", sa.Text(), nullable=True))
        _add_if_missing(batch, existing, "trigger_emojis", sa.Column("trigger_emojis", sa.JSON(), nullable=True))
        _add_if_missing(batch, existing, "mood", sa.Column("mood", sa.String(length=64), nullable=True))
        _add_if_missing(batch, existing, "gender_target", sa.Column("gender_target", sa.String(length=16), nullable=False, server_default="neutral"))
        _add_if_missing(batch, existing, "relationship_stages", sa.Column("relationship_stages", sa.JSON(), nullable=True))
        _add_if_missing(batch, existing, "enabled", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        _add_if_missing(batch, existing, "probability", sa.Column("probability", sa.Float(), nullable=False, server_default="1"))
        _add_if_missing(batch, existing, "daily_limit", sa.Column("daily_limit", sa.Integer(), nullable=True))
    op.execute("UPDATE sticker_items SET category = 'normal' WHERE category IS NULL OR category = ''")
    op.execute("UPDATE sticker_items SET gender_target = 'neutral' WHERE gender_target IS NULL OR gender_target = ''")
    op.execute("UPDATE sticker_items SET enabled = true WHERE enabled IS NULL")
    op.execute("UPDATE sticker_items SET probability = 1 WHERE probability IS NULL")
    for name, cols in {
        "ix_sticker_items_key": ["key"],
        "ix_sticker_items_category": ["category"],
        "ix_sticker_items_mood": ["mood"],
        "ix_sticker_items_gender_target": ["gender_target"],
        "ix_sticker_items_enabled": ["enabled"],
    }.items():
        try:
            op.create_index(name, "sticker_items", cols)
        except Exception:
            pass


def downgrade() -> None:
    for name in ["ix_sticker_items_enabled", "ix_sticker_items_gender_target", "ix_sticker_items_mood", "ix_sticker_items_category", "ix_sticker_items_key"]:
        try:
            op.drop_index(name, table_name="sticker_items")
        except Exception:
            pass
    existing = _columns("sticker_items")
    with op.batch_alter_table("sticker_items") as batch:
        for name in ["daily_limit", "probability", "enabled", "relationship_stages", "gender_target", "mood", "trigger_emojis", "meaning", "category", "key"]:
            if name in existing:
                batch.drop_column(name)
