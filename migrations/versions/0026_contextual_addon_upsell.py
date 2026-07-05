"""contextual add-on upsell

Revision ID: 0026_contextual_addon_upsell
Revises: 0025_paid_addons
Create Date: 2026-07-05
"""
from alembic import op

revision = "0026_contextual_addon_upsell"
down_revision = "0025_paid_addons"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS addon_upsell_events (
      id serial primary key,
      user_id integer not null references users(id),
      addon_key varchar not null,
      event_type varchar not null,
      reason text,
      score numeric,
      message_id integer null references messages(id),
      metadata_json jsonb,
      created_at timestamp default now()
    )
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_addon_upsell_events_user_addon_created
    ON addon_upsell_events(user_id, addon_key, created_at)
    """)
    op.execute("""
        UPDATE addon_products
        SET metadata_json = coalesce(metadata_json, '{}'::jsonb) || '{"upsell_enabled": true, "requires_adult": true, "trigger_keywords": ["سکسچت", "چت بزرگسال", "بزرگسال", "شیطون‌تر", "شیطون تر", "صمیمی‌تر", "صمیمی تر", "نزدیک‌تر شو", "نزدیک تر شو", "هنوز زوده", "بذار بیشتر آشنا شیم", "چرا نمیذاری", "چرا نمی‌ذاری"], "negative_keywords": ["زیر ۱۸", "زیر18", "بچه", "نوجوان", "اجبار", "زور", "تجاوز", "بی‌رضایت", "بی رضایت"], "min_score": 0.6, "cooldown_hours": 24, "max_suggestions_per_7d": 2, "upsell_title": "🔥 افزایش صمیمیت رابطه", "upsell_text": "اگه می‌خوای رابطه‌تون سریع‌تر از حالت آشنایی رد بشه و صمیمی‌تر بشه، این افزودنی سطح صمیمیت مونس رو به بالاترین درجه می‌رسونه.", "cta_text": "فعال‌کردن افزایش صمیمیت", "management_deeplink": "https://t.me/moonesaibot?start=addon_intimacy_max_unlock"}'::jsonb,
            updated_at = now()
        WHERE key = 'intimacy_max_unlock'
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_addon_upsell_events_user_addon_created")
    op.execute("DROP TABLE IF EXISTS addon_upsell_events")
