import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.settings import AppSetting

DEFAULT_SETTINGS = {
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
 "proactive.daily_max_per_user": ("1", "integer", "Daily proactive message cap per user"),
 "proactive.quiet_hours_start": ("00:00", "string", "Proactive quiet hours start"),
 "proactive.quiet_hours_end": ("10:00", "string", "Proactive quiet hours end"),
 "proactive.allowed_plans": ("vip,plus,basic,mini,free,daily,free_daily,none,trial", "string", "Comma-separated proactive eligible plans"),
 "proactive.inactive_after_hours": ("6", "integer", "Inactive hours before proactive eligibility"),
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
