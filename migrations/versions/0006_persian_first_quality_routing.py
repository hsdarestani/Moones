"""persian first quality routing

Revision ID: 0006_persian_first_quality_routing
Revises: 0005_persona_voice_debug
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_persian_first_quality_routing"
down_revision = "0005_persona_voice_debug"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("last_detected_language", sa.String(16), nullable=True))
        batch.add_column(sa.Column("last_quality_gate_result", sa.String(32), nullable=True))
        batch.add_column(sa.Column("last_quality_gate_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("last_quality_gate_rejected", sa.Boolean(), nullable=False, server_default=sa.false()))
    settings = [
        ("llm.venice.model", "zai-org-glm-5-1", "string", "Default Venice model"),
        ("llm.primary_persian_model", "zai-org-glm-5-1", "string", "Primary Persian chat model"),
        ("llm.roleplay_model", "venice-uncensored-role-play", "string", "English roleplay model"),
        ("llm.allow_persian_uncensored_roleplay", "false", "boolean", "Allow uncensored roleplay model for Persian"),
        ("quality_gate.enabled", "true", "boolean", "Enable response quality gate"),
        ("humanizer.enabled", "true", "boolean", "Enable Persian humanizer"),
        ("emoji.probability", "0.15", "float", "Emoji probability"),
        ("emoji.max_per_message", "1", "integer", "Max emoji"),
    ]
    conn = op.get_bind()
    for key, value, typ, desc in settings:
        conn.execute(sa.text("DELETE FROM app_settings WHERE key = :key"), {"key": key})
        conn.execute(sa.text("INSERT INTO app_settings (key, value, value_type, description) VALUES (:key, :value, :typ, :desc)"), {"key": key, "value": value, "typ": typ, "desc": desc})


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("last_quality_gate_rejected")
        batch.drop_column("last_quality_gate_reason")
        batch.drop_column("last_quality_gate_result")
        batch.drop_column("last_detected_language")
