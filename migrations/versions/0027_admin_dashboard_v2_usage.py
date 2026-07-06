"""admin dashboard v2 usage accounting

Revision ID: 0027_admin_dashboard_v2_usage
Revises: 0026_contextual_addon_upsell
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0027_admin_dashboard_v2_usage"
down_revision = "0026_contextual_addon_upsell"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("media_message_id", sa.Integer(), sa.ForeignKey("media_messages.id"), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), server_default="venice", nullable=False),
        sa.Column("feature", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("audio_seconds", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("image_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("character_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unit_input_usd", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("unit_output_usd", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("unit_audio_second_usd", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("unit_image_usd", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("unit_character_usd", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("cost_toman", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("status", sa.Text(), server_default="success", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_ai_usage_events_user_created", "ai_usage_events", ["user_id", sa.text("created_at DESC")])
    op.create_index("idx_ai_usage_events_feature_created", "ai_usage_events", ["feature", sa.text("created_at DESC")])
    op.create_index("idx_ai_usage_events_model_created", "ai_usage_events", ["model", sa.text("created_at DESC")])
    op.create_table(
        "ai_usage_daily_rollups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column("feature", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("audio_seconds", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("image_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("character_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("cost_toman", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("date", "user_id", "plan", "feature", "model", name="uq_ai_usage_rollup_dim"),
    )


def downgrade():
    op.drop_table("ai_usage_daily_rollups")
    op.drop_index("idx_ai_usage_events_model_created", table_name="ai_usage_events")
    op.drop_index("idx_ai_usage_events_feature_created", table_name="ai_usage_events")
    op.drop_index("idx_ai_usage_events_user_created", table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
