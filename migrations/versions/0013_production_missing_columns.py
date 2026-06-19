"""production missing columns

Revision ID: 0013_production_missing_columns
Revises: 0012_token_plans_voice_stickers
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_production_missing_columns"
down_revision = "0012_token_plans_voice_stickers"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _add_if_missing(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    _add_if_missing("daily_usage", sa.Column("llm_requests", sa.Integer(), nullable=False, server_default="0"))
    _add_if_missing("daily_usage", sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"))
    _add_if_missing("daily_usage", sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"))
    _add_if_missing("daily_usage", sa.Column("voice_tokens", sa.Integer(), nullable=False, server_default="0"))
    _add_if_missing("daily_usage", sa.Column("daily_voice_sent", sa.Integer(), nullable=False, server_default="0"))
    _add_if_missing("daily_usage", sa.Column("daily_stickers_sent", sa.Integer(), nullable=False, server_default="0"))
    _add_if_missing("sticker_items", sa.Column("persona_gender", sa.String(32), nullable=True))
    _add_if_missing("sticker_items", sa.Column("persona_style", sa.String(64), nullable=True))
    _add_if_missing("sticker_items", sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))


def downgrade() -> None:
    # Production repair migration: never drop potentially live data.
    pass
