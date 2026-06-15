import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.settings import AppSetting

DEFAULT_SETTINGS = {
 "subscription.daily.price_coins": ("100", "integer", "Daily plan price"),
 "subscription.weekly.price_coins": ("500", "integer", "Weekly plan price"),
 "subscription.monthly.price_coins": ("1500", "integer", "Monthly plan price"),
 "limits.free.daily_messages": ("30", "integer", "Free daily messages"),
 "limits.daily.daily_messages": ("500", "integer", "Daily pass cap"),
 "limits.weekly.daily_messages": ("500", "integer", "Weekly pass cap"),
 "limits.monthly.daily_messages": ("500", "integer", "Monthly pass cap"),
 "payment.link": (get_settings().payment_link, "string", "Manual payment link"),
 "support.username": ("", "string", "Support username"),
 "llm.venice.model": ("zai-org-glm-5-1", "string", "Default Venice model"),
 "llm.primary_persian_model": ("zai-org-glm-5-1", "string", "Primary Persian chat model"),
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
