import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.settings import AppSetting

DEFAULT_SETTINGS = {
 "billing.usd_to_toman": ("60000", "float", "USD to Toman exchange rate for cost reports"),
 "pricing.venice.qwen-3-6-plus.input_per_1m_usd": ("0.63", "float", "Qwen input price per 1M tokens"),
 "pricing.venice.qwen-3-6-plus.output_per_1m_usd": ("3.75", "float", "Qwen output price per 1M tokens"),
 "pricing.venice.qwen-3-6-plus.long_context_input_per_1m_usd": ("2.50", "float", "Long-context input price"),
 "pricing.venice.qwen-3-6-plus.long_context_output_per_1m_usd": ("7.50", "float", "Long-context output price"),
 "pricing.venice.long_context_threshold_tokens": ("256000", "integer", "Long-context token threshold"),
 "pricing.venice.openai/whisper-large-v3.per_audio_second_usd": ("0.0001", "float", "STT audio second price"),
 "pricing.venice.nvidia/parakeet-tdt-0.6b-v3.per_audio_second_usd": ("0.0001", "float", "STT audio second price"),
 "pricing.venice.fal-ai/wizper.per_audio_second_usd": ("0.0001", "float", "STT audio second price"),
 "pricing.venice.elevenlabs/scribe-v2.per_audio_second_usd": ("0.000167", "float", "STT audio second price"),
 "pricing.venice.stt-xai-v1.per_audio_second_usd": ("0.00003148", "float", "STT audio second price"),
 "pricing.venice.qwen3-vl-235b-a22b.input_per_1m_usd": ("0", "float", "Vision input price"),
 "pricing.venice.qwen3-vl-235b-a22b.output_per_1m_usd": ("0", "float", "Vision output price"),
 "pricing.venice.e2ee-qwen3-vl-30b-a3b-p.input_per_1m_usd": ("0", "float", "Vision input price"),
 "pricing.venice.e2ee-qwen3-vl-30b-a3b-p.output_per_1m_usd": ("0", "float", "Vision output price"),
 "subscription.mini.price_coins": ("590000", "integer", "Mini plan price"),
 "subscription.basic.price_coins": ("990000", "integer", "Basic plan price"),
 "subscription.plus.price_coins": ("2290000", "integer", "Plus plan price"),
 "subscription.vip.price_coins": ("4900000", "integer", "VIP plan price"),
 "limits.free.daily_token_limit": ("20000", "integer", "Free daily usage capacity"),
 "limits.mini.daily_token_limit": ("80000", "integer", "Mini daily usage capacity"),
 "limits.basic.daily_token_limit": ("150000", "integer", "Basic daily usage capacity"),
 "limits.plus.daily_token_limit": ("500000", "integer", "Plus daily usage capacity"),
 "limits.vip.daily_token_limit": ("1200000", "integer", "VIP daily usage capacity"),
 "payment.link": (get_settings().payment_link, "string", "Manual payment link"),
 "addon_intimacy_max_price_toman": ("100000", "integer", "Intimacy max add-on price"),
 "addon_intimacy_max_enabled": ("true", "boolean", "Enable intimacy max add-on"),
 "addon_intimacy_max_title": ("افزایش صمیمیت رابطه", "string", "Intimacy max add-on title"),
 "support.username": ("", "string", "Support username"),
 "llm.venice.model": ("qwen-3-6-plus", "string", "Default Venice model slug"),
 "llm.primary_persian_model": ("qwen-3-6-plus", "string", "Primary Persian chat model"),
 "llm.prompt_mode": ("simple_partner_v2", "string", "Production prompt mode"),
 "llm.roleplay_model": ("venice-uncensored-role-play", "string", "English roleplay model"),
 "llm.allow_persian_uncensored_roleplay": ("false", "boolean", "Allow uncensored roleplay model for Persian"),
 "quality_gate.enabled": ("true", "boolean", "Enable response quality gate"),
 "humanizer.enabled": ("true", "boolean", "Enable Persian humanizer"),
 "stickers.enabled": ("true", "boolean", "Enable stickers"),
 "stickers.probability": ("0.12", "float", "Sticker probability"),
 "stickers.max_per_day_per_user": ("10", "integer", "Daily sticker cap"),
 "emoji.enabled": ("true", "boolean", "Enable emoji"),
 "emoji.probability": ("0.15", "float", "Emoji probability"),
 "emoji.max_per_message": ("1", "integer", "Max emoji"),
 "proactive.enabled": ("true", "boolean", "Enable proactive partner messages"),
 "proactive.scheduler_tick_seconds": ("900", "integer", "Proactive scheduler tick seconds"),
 "proactive.random_enabled": ("true", "boolean", "Randomize proactive schedule intervals"),
 "proactive.free.min_hours": ("18", "float", "Free proactive minimum interval hours"),
 "proactive.free.max_hours": ("36", "float", "Free proactive maximum interval hours"),
 "proactive.mini.min_hours": ("10", "float", "Mini proactive minimum interval hours"),
 "proactive.mini.max_hours": ("24", "float", "Mini proactive maximum interval hours"),
 "proactive.basic.min_hours": ("6", "float", "Basic proactive minimum interval hours"),
 "proactive.basic.max_hours": ("18", "float", "Basic proactive maximum interval hours"),
 "proactive.plus.min_hours": ("3", "float", "Plus proactive minimum interval hours"),
 "proactive.plus.max_hours": ("9", "float", "Plus proactive maximum interval hours"),
 "proactive.vip.min_hours": ("2", "float", "VIP proactive minimum interval hours"),
 "proactive.vip.max_hours": ("6", "float", "VIP proactive maximum interval hours"),
 "proactive.default.min_hours": ("8", "float", "Default proactive minimum interval hours"),
 "proactive.default.max_hours": ("24", "float", "Default proactive maximum interval hours"),
 "proactive.min_hours_between_messages": ("1", "integer", "Safety minimum hours between proactive messages"),
 "proactive.daily_max_per_user": ("2", "integer", "Daily proactive message cap per user"),
 "proactive.send_window_start": ("10:30", "string", "Proactive allowed send window start (server/local time)"),
 "proactive.send_window_end": ("23:30", "string", "Proactive allowed send window end (server/local time)"),
 "proactive.quiet_hours_start": ("00:00", "string", "Legacy proactive quiet hours start"),
 "proactive.quiet_hours_end": ("10:00", "string", "Legacy proactive quiet hours end"),
 "proactive.allowed_plans": ("vip,plus,basic,mini,free,daily,free_daily,none,trial", "string", "Comma-separated proactive eligible plans"),
 "proactive.inactive_after_hours": ("6", "integer", "Inactive hours before proactive eligibility"),
 "human_presence.enabled": ("true", "boolean", "Enable unified human presence engine"),
 "human_delivery.enabled": ("true", "boolean", "Enable human delivery shaping"),
 "human_delivery.multi_message.enabled": ("true", "boolean", "Enable multi-bubble text delivery"),
 "human_delivery.multi_message.free_rate": ("0.15", "float", "Free multi-bubble rate"),
 "human_delivery.multi_message.mini_rate": ("0.18", "float", "Mini multi-bubble rate"),
 "human_delivery.multi_message.basic_rate": ("0.25", "float", "Basic multi-bubble rate"),
 "human_delivery.multi_message.plus_rate": ("0.38", "float", "Plus multi-bubble rate"),
 "human_delivery.multi_message.vip_rate": ("0.45", "float", "VIP multi-bubble rate"),
 "human_delivery.afterthought.enabled": ("true", "boolean", "Enable delayed afterthoughts"),
 "human_delivery.afterthought.daily_free": ("1", "integer", "Free afterthought cap"),
 "human_delivery.afterthought.daily_mini": ("1", "integer", "Mini afterthought cap"),
 "human_delivery.afterthought.daily_basic": ("2", "integer", "Basic afterthought cap"),
 "human_delivery.afterthought.daily_plus": ("3", "integer", "Plus afterthought cap"),
 "human_delivery.afterthought.daily_vip": ("4", "integer", "VIP afterthought cap"),
 "human_delivery.afterthought.min_delay_seconds": ("8", "integer", "Afterthought min delay"),
 "human_delivery.afterthought.max_delay_seconds": ("75", "integer", "Afterthought max delay"),
 "human_delivery.interjection.enabled": ("true", "boolean", "Enable rare interjections"),
 "human_delivery.interjection.daily_free": ("0", "integer", "Free interjection cap"),
 "human_delivery.interjection.daily_mini": ("1", "integer", "Mini interjection cap"),
 "human_delivery.interjection.daily_basic": ("1", "integer", "Basic interjection cap"),
 "human_delivery.interjection.daily_plus": ("2", "integer", "Plus interjection cap"),
 "human_delivery.interjection.daily_vip": ("3", "integer", "VIP interjection cap"),
 "human_delivery.interjection.min_recent_gap_seconds": ("4", "integer", "Interjection recent bot gap"),
 "human_delivery.interjection.active_window_seconds": ("90", "integer", "Interjection active window"),
 "human_presence.micro_rituals.enabled": ("true", "boolean", "Enable micro rituals"),
 "human_presence.boundaries.enabled": ("true", "boolean", "Enable soft boundaries"),
 "human_presence.controlled_disagreement.enabled": ("true", "boolean", "Enable safe disagreement"),
 "human_presence.question_spam_guard.enabled": ("true", "boolean", "Enable question spam guard"),
 "human_presence.max_bot_bubbles_2min": ("4", "integer", "Max bot text bubbles per two minutes"),
}
class SettingsService:
    def seed_defaults(self, db: Session):
        for key,(value,typ,desc) in DEFAULT_SETTINGS.items():
            if not db.scalar(select(AppSetting).where(AppSetting.key==key)):
                db.add(AppSetting(key=key,value=value,value_type=typ,description=desc))
        db.flush()
    def get(self, db: Session, key: str, default=None):
        row=db.scalar(select(AppSetting).where(AppSetting.key==key)); return row.value if row else default
    def get_int(self, db, key, default=0):
        try: return int(self.get(db,key,default))
        except Exception: return default
    def get_float(self, db, key, default=0.0):
        try: return float(self.get(db,key,default))
        except Exception: return default
    def get_str(self, db, key, default=""):
        return str(self.get(db,key,default) or default)
    def get_bool(self, db, key, default=False):
        return str(self.get(db,key,str(default))).lower() in {"1","true","yes","on"}
    def set_value(self, db,key,value,value_type="string",admin_id=None):
        row=db.scalar(select(AppSetting).where(AppSetting.key==key))
        if not row:
            row=AppSetting(key=key,value=str(value),value_type=value_type); db.add(row)
        row.value=json.dumps(value,ensure_ascii=False) if value_type=="json" and not isinstance(value,str) else str(value)
        row.value_type=value_type; row.updated_by_admin_id=admin_id; db.flush(); return row
