from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.settings import AppSetting

CATEGORY_WALLET = "کیف پول و قیمت‌گذاری"
CATEGORY_RECOMMENDATIONS = "پیشنهادهای شارژ"
CATEGORY_CHAT_MODELS = "مدل‌های گفتگو"
CATEGORY_INPUT_MEDIA = "ورودی عکس و وویس"
CATEGORY_VOICE_OUTPUT = "خروجی وویس"
CATEGORY_MOONES_IMAGES = "تصاویر مونس"
CATEGORY_IMAGE_SAFETY = "ایمنی تصاویر"
CATEGORY_MEDIA_ARCHIVE = "آرشیو رسانه"
CATEGORY_PROACTIVE = "پیام‌های خودجوش"
CATEGORY_TELEGRAM = "تلگرام و ارسال"
CATEGORY_ADDONS = "افزودنی‌ها"
CATEGORY_OPERATIONS = "عملیات و هشدارها"
CATEGORY_LEGACY_SUBS = "اشتراک‌های قدیمی"
CATEGORY_ADVANCED = "تنظیمات پیشرفته"
SETTING_CATEGORIES = [CATEGORY_WALLET, CATEGORY_RECOMMENDATIONS, CATEGORY_CHAT_MODELS, CATEGORY_INPUT_MEDIA, CATEGORY_VOICE_OUTPUT, CATEGORY_MOONES_IMAGES, CATEGORY_IMAGE_SAFETY, CATEGORY_MEDIA_ARCHIVE, CATEGORY_PROACTIVE, CATEGORY_TELEGRAM, CATEGORY_ADDONS, CATEGORY_OPERATIONS, CATEGORY_LEGACY_SUBS, CATEGORY_ADVANCED]

DEFAULT_SETTINGS = {
 "billing.usd_to_toman": (str(get_settings().billing_usd_to_toman), "decimal", "نرخ تبدیل دلار به تومان"),
 "billing.profit_margin_percent": ("100", "decimal", "درصد سود فروش سکه؛ مقدار امن ۰ تا ۱۰۰۰"),
 "billing.signup_bonus_coins": ("200", "integer", "هدیه ثبت‌نام به سکه"),
 "admin.coin_campaign.large_total_coins": ("1000000", "integer", "Large coin campaign total requiring password re-verification"),
 "admin.coin_campaign.max_coins_per_user": ("100000", "integer", "Maximum coins per user in an admin coin campaign"),
 "wallet.recommendation.starter_coins": ("1000", "integer", "پیشنهاد شارژ شروع"),
 "wallet.recommendation.regular_coins": ("3000", "integer", "پیشنهاد شارژ روزمره"),
 "wallet.recommendation.heavy_coins": ("5000", "integer", "پیشنهاد شارژ پرتر"),
 "wallet.recommendation.default_coins": ("3000", "integer", "پیشنهاد پیش‌فرض شارژ"),
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
 "subscriptions.new_sales_enabled": ("false", "boolean", "Enable new experience membership sales"),
 "subscription.mini.price_coins": ("5900", "integer", "Mini plan price"),
 "subscription.basic.price_coins": ("9900", "integer", "Basic plan price"),
 "subscription.plus.price_coins": ("22900", "integer", "Plus plan price"),
 "subscription.vip.price_coins": ("49000", "integer", "VIP plan price"),
 "limits.free.daily_token_limit": ("20000", "integer", "Free daily usage capacity"),
 "limits.mini.daily_token_limit": ("80000", "integer", "Mini daily usage capacity"),
 "limits.basic.daily_token_limit": ("150000", "integer", "Basic daily usage capacity"),
 "limits.plus.daily_token_limit": ("500000", "integer", "Plus daily usage capacity"),
 "limits.vip.daily_token_limit": ("1200000", "integer", "VIP daily usage capacity"),
 "generated_media.forward_enabled": ("false", "boolean", "Forward generated media to archive group"),
 "generated_media.chat_id": ("", "telegram_chat_id", "Generated media archive chat id"),
 "generated_media.forward_images": ("true", "boolean", "Forward generated images"),
 "generated_media.forward_voices": ("true", "boolean", "Forward generated voices"),
 "generated_media.fallback_to_support_media_chat_id": ("false", "boolean", "Use support media chat id when generated media chat id is empty"),
 "image_generation.adult_enabled": ("false", "boolean", "Allow adult image generation"),
 "image_generation.soft_safety_enabled": ("true", "boolean", "Enable optional soft image safety classifier"),
 "image_generation.pipeline_v2_enabled": ("false", "boolean", "Enable deterministic Image Pipeline v2"),
 "image_generation.pipeline_v2_shadow_mode": ("false", "boolean", "Run Image Pipeline v2 in shadow without using execution result"),
 "image_generation.pipeline_v2_production_approved": ("false", "boolean", "Production readiness gate for deterministic Image Pipeline v2"),
 "image_generation.semantic_router_enabled": ("false", "boolean", "Enable semantic image router execution"),
 "image_generation.semantic_router_production_approved": ("false", "boolean", "Production approval gate for semantic image router execution"),
 "image_generation.semantic_router_allowed_user_ids": ("", "string", "Comma-separated user IDs enabled for semantic image router execution"),
 "image_generation.semantic_router_shadow_mode": ("false", "boolean", "Read-only shadow logging for semantic image intent router"),
 "image_generation.semantic_router_provider": ("venice", "string", "Semantic image intent router provider"),
 "image_generation.semantic_router_model": ("qwen-3-6-plus", "string", "Semantic image intent router model"),
 "image_generation.semantic_router_thresholds": ("{\"generate_new\":0.82,\"refine_previous\":0.84,\"variation\":0.84,\"resend_exact\":0.90,\"chat\":0.70,\"clarify_below\":0.74}", "json", "Calibrated semantic router confidence thresholds"),
 "media_retention.full_image_hours": ("0", "integer", "Hours to retain full generated image bytes after delivery"),
 "media_retention.voice_ogg_days": ("30", "integer", "Days to retain generated voice audio"),
 "payment.link": (get_settings().payment_link, "url", "Manual payment link"),
 "addon_intimacy_max_price_toman": ("100000", "integer", "Intimacy max add-on price"),
 "addon_intimacy_max_enabled": ("true", "boolean", "Enable intimacy max add-on"),
 "addon_intimacy_max_title": ("افزایش صمیمیت رابطه", "string", "Intimacy max add-on title"),
 "support.username": ("", "string", "Support username"),
 "llm.venice.model": ("qwen-3-6-plus", "enum", "Default Venice model slug"),
 "llm.primary_persian_model": ("qwen-3-6-plus", "enum", "Primary Persian chat model"),
 "llm.prompt_mode": ("simple_partner_v2", "enum", "Production prompt mode"),
 "llm.roleplay_model": ("venice-uncensored-role-play", "enum", "English roleplay model"),
 "llm.allow_persian_uncensored_roleplay": ("false", "boolean", "Allow uncensored roleplay model for Persian"),
 "quality_gate.enabled": ("true", "boolean", "Enable response quality gate"),
 "humanizer.enabled": ("true", "boolean", "Enable Persian humanizer"),
 "stickers.enabled": ("true", "boolean", "Enable stickers"),
 "stickers.probability": ("0.12", "decimal", "Sticker probability"),
 "stickers.max_per_day_per_user": ("10", "integer", "Daily sticker cap"),
 "emoji.enabled": ("true", "boolean", "Enable emoji"),
 "emoji.probability": ("0.15", "decimal", "Emoji probability"),
 "emoji.max_per_message": ("1", "integer", "Max emoji"),
 "roleplay.default_city": ("تهران", "string", "نقش‌آفرینی و زمان: شهر پیش‌فرض"),
 "roleplay.default_timezone": ("Asia/Tehran", "string", "نقش‌آفرینی و زمان: منطقه زمانی پیش‌فرض"),
 "proactive.enabled": ("true", "boolean", "Enable proactive partner messages"),
 "proactive.scheduler_tick_seconds": ("900", "integer", "Proactive scheduler tick seconds"),
 "proactive.random_enabled": ("true", "boolean", "Randomize proactive schedule intervals"),
 "proactive.free.min_hours": ("18", "decimal", "Free proactive minimum interval hours"),
 "proactive.free.max_hours": ("36", "decimal", "Free proactive maximum interval hours"),
 "proactive.mini.min_hours": ("10", "decimal", "Mini proactive minimum interval hours"),
 "proactive.mini.max_hours": ("24", "decimal", "Mini proactive maximum interval hours"),
 "proactive.basic.min_hours": ("6", "decimal", "Basic proactive minimum interval hours"),
 "proactive.basic.max_hours": ("18", "decimal", "Basic proactive maximum interval hours"),
 "proactive.plus.min_hours": ("3", "decimal", "Plus proactive minimum interval hours"),
 "proactive.plus.max_hours": ("9", "decimal", "Plus proactive maximum interval hours"),
 "proactive.vip.min_hours": ("2", "decimal", "VIP proactive minimum interval hours"),
 "proactive.vip.max_hours": ("6", "decimal", "VIP proactive maximum interval hours"),
 "proactive.default.min_hours": ("8", "decimal", "Default proactive minimum interval hours"),
 "proactive.default.max_hours": ("24", "decimal", "Default proactive maximum interval hours"),
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
 "human_delivery.multi_message.free_rate": ("0.15", "decimal", "Free multi-bubble rate"),
 "human_delivery.multi_message.mini_rate": ("0.18", "decimal", "Mini multi-bubble rate"),
 "human_delivery.multi_message.basic_rate": ("0.25", "decimal", "Basic multi-bubble rate"),
 "human_delivery.multi_message.plus_rate": ("0.38", "decimal", "Plus multi-bubble rate"),
 "human_delivery.multi_message.vip_rate": ("0.45", "decimal", "VIP multi-bubble rate"),
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
 "human_delivery.interjection.daily_mini": ("1", "integer", "Daily interjection cap"),
 "human_delivery.interjection.daily_basic": ("1", "integer", "Daily interjection cap"),
 "human_delivery.interjection.daily_plus": ("2", "integer", "Daily interjection cap"),
 "human_delivery.interjection.daily_vip": ("3", "integer", "Daily interjection cap"),
 "human_delivery.interjection.min_recent_gap_seconds": ("4", "integer", "Interjection recent bot gap"),
 "human_delivery.interjection.active_window_seconds": ("90", "integer", "Interjection active window"),
 "human_presence.micro_rituals.enabled": ("true", "boolean", "Enable micro rituals"),
 "human_presence.boundaries.enabled": ("true", "boolean", "Enable soft boundaries"),
 "human_presence.controlled_disagreement.enabled": ("true", "boolean", "Enable safe disagreement"),
 "human_presence.question_spam_guard.enabled": ("true", "boolean", "Enable question spam guard"),
 "human_presence.max_bot_bubbles_2min": ("4", "integer", "Max bot text bubbles per two minutes"),
}

MODEL_ALLOWLIST = {"qwen-3-6-plus", "qwen3-vl-235b-a22b", "e2ee-qwen3-vl-30b-a3b-p", "venice-uncensored-role-play", "openai/whisper-large-v3", "nvidia/parakeet-tdt-0.6b-v3", "fal-ai/wizper", "elevenlabs/scribe-v2", "stt-xai-v1"}
PROMPT_MODES = {"simple_partner_v2", "simple_partner", "roleplay"}

@dataclass(frozen=True)
class SettingMeta:
    key: str
    label: str
    description: str
    category: str
    type: str
    default: str
    required_permission: str = "settings.nonfinancial"
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    enum_values: tuple[str, ...] = ()
    restart_required: bool = False
    sensitive: bool = False
    advanced: bool = False
    affected_feature: str = "تنظیمات سیستم"
    allow_negative: bool = False
    validation_rules: dict[str, Any] = field(default_factory=dict)

    def public_default(self) -> str:
        return "[configured]" if self.sensitive and self.default else self.default

def _category_for(key: str) -> str:
    if key.startswith(("billing.", "pricing.")): return CATEGORY_WALLET
    if key.startswith("wallet.recommendation."): return CATEGORY_RECOMMENDATIONS
    if key.startswith(("llm.", "quality_gate", "humanizer", "roleplay")): return CATEGORY_CHAT_MODELS
    if "stt" in key or "vision" in key: return CATEGORY_INPUT_MEDIA
    if "tts" in key or "voice" in key: return CATEGORY_VOICE_OUTPUT
    if key.startswith("image_generation."): return CATEGORY_IMAGE_SAFETY
    if key.startswith(("generated_media.", "media_retention.")): return CATEGORY_MEDIA_ARCHIVE
    if key.startswith(("proactive.", "human_delivery", "human_presence", "emoji", "stickers")): return CATEGORY_PROACTIVE
    if key.startswith(("payment.", "support.")): return CATEGORY_TELEGRAM
    if key.startswith("addon_"): return CATEGORY_ADDONS
    if key.startswith("admin."): return CATEGORY_OPERATIONS
    if key.startswith(("subscription.", "subscriptions.", "limits.")): return CATEGORY_LEGACY_SUBS
    return CATEGORY_ADVANCED

def _permission_for(key: str, category: str) -> str:
    if category in {CATEGORY_WALLET, CATEGORY_RECOMMENDATIONS, CATEGORY_LEGACY_SUBS, CATEGORY_ADDONS}: return "settings.billing"
    if category == CATEGORY_IMAGE_SAFETY: return "settings.safety"
    if category in {CATEGORY_MEDIA_ARCHIVE, CATEGORY_PROACTIVE, CATEGORY_OPERATIONS}: return "settings.operations"
    return "settings.nonfinancial"

def _label_for(key: str, desc: str) -> str:
    labels = {"billing.usd_to_toman":"نرخ دلار به تومان", "billing.profit_margin_percent":"حاشیه فروش کیف پول", "billing.signup_bonus_coins":"هدیه کیف پول", "generated_media.chat_id":"چت آرشیو رسانه‌های ساخته‌شده", "image_generation.adult_enabled":"فعال‌سازی دریافت عکس بزرگسال", "image_generation.soft_safety_enabled":"کنترل نرم ایمنی تصاویر", "subscriptions.new_sales_enabled":"فروش اشتراک‌های قدیمی", "payment.link":"لینک پرداخت", "support.username":"نام کاربری پشتیبانی"}
    return labels.get(key, desc or key)

def _meta_for(key: str, default: str, typ: str, desc: str) -> SettingMeta:
    typ = {"float": "decimal"}.get(typ, typ)
    category = _category_for(key)
    min_v = Decimal("0") if typ in {"integer", "decimal"} else None
    max_v = None
    enum_values: tuple[str, ...] = ()
    if "percent" in key or key.endswith("probability") or key.endswith("_rate"):
        max_v = Decimal("1000") if "profit_margin" in key else Decimal("1")
    if key.startswith("llm.") and key.endswith("model"):
        typ = "enum"; enum_values = tuple(sorted(MODEL_ALLOWLIST))
    if key == "llm.prompt_mode":
        typ = "enum"; enum_values = tuple(sorted(PROMPT_MODES))
    sensitive = any(s in key.lower() for s in ("token", "api_key", "password", "database_url", "secret"))
    return SettingMeta(key=key, label=_label_for(key, desc), description=desc, category=category, type=typ, default=str(default or ""), required_permission=_permission_for(key, category), min_value=min_v, max_value=max_v, enum_values=enum_values, restart_required=key.startswith(("llm.", "pricing.")), sensitive=sensitive, affected_feature=category, validation_rules={"required": False})

SETTING_REGISTRY = {k: _meta_for(k, *v) for k, v in DEFAULT_SETTINGS.items()}

class SettingsValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))

def mask_value(value: Any, meta: SettingMeta | None = None) -> str:
    if meta and meta.sensitive:
        return "configured" if str(value or "") else "not configured"
    return str(value if value is not None else "")

def validate_setting_value(meta: SettingMeta, raw: Any) -> Any:
    value = "" if raw is None else str(raw).strip()
    if meta.type == "boolean":
        low = value.lower()
        if low in {"1", "true", "yes", "on", "فعال"}: return True
        if low in {"0", "false", "no", "off", "غیرفعال"}: return False
        raise ValueError("مقدار بولی نامعتبر است")
    if meta.type == "integer":
        if not re.fullmatch(r"[-+]?\d+", value): raise ValueError("عدد صحیح نامعتبر است")
        parsed = int(value)
    elif meta.type == "decimal":
        try: parsed = Decimal(value)
        except (InvalidOperation, ValueError): raise ValueError("عدد اعشاری نامعتبر است")
    elif meta.type == "enum":
        if value not in meta.enum_values: raise ValueError("مقدار انتخابی پشتیبانی نمی‌شود")
        return value
    elif meta.type == "telegram_chat_id":
        if value and not re.fullmatch(r"-?\d{5,20}|@[A-Za-z0-9_]{5,32}", value): raise ValueError("شناسه چت تلگرام نامعتبر است")
        return value
    elif meta.type == "url":
        if value:
            parsed_url = urlparse(value)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc: raise ValueError("URL نامعتبر است")
        return value
    elif meta.type == "json":
        try: return json.loads(value) if value else None
        except json.JSONDecodeError: raise ValueError("JSON نامعتبر است")
    else:
        return value
    if meta.min_value is not None and Decimal(str(parsed)) < meta.min_value: raise ValueError("کمتر از حد مجاز است")
    if meta.max_value is not None and Decimal(str(parsed)) > meta.max_value: raise ValueError("بیشتر از حد مجاز است")
    return parsed

def serialize_setting_value(meta: SettingMeta, value: Any) -> str:
    if meta.type == "boolean": return "true" if bool(value) else "false"
    if meta.type == "json": return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else ""
    return str(value)

class SettingsService:
    def seed_defaults(self, db: Session):
        for key, meta in SETTING_REGISTRY.items():
            if not db.scalar(select(AppSetting).where(AppSetting.key==key)):
                db.add(AppSetting(key=key,value=meta.default,value_type=meta.type,description=meta.description))
        db.flush()
    def registry(self): return SETTING_REGISTRY
    def get_meta(self, key: str) -> SettingMeta | None: return SETTING_REGISTRY.get(key)
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
        meta = SETTING_REGISTRY.get(key)
        if meta:
            value = validate_setting_value(meta, value)
            value_type = meta.type
            stored = serialize_setting_value(meta, value)
        else:
            stored = json.dumps(value,ensure_ascii=False) if value_type=="json" and not isinstance(value,str) else str(value)
        row=db.scalar(select(AppSetting).where(AppSetting.key==key))
        if not row:
            row=AppSetting(key=key,value=stored,value_type=value_type); db.add(row)
        row.value=stored; row.value_type=value_type; row.updated_by_admin_id=admin_id; db.flush(); return row
    def validate_changes(self, changes: dict[str, Any]) -> dict[str, Any]:
        parsed = {}; errors = {}
        for key, raw in changes.items():
            meta = SETTING_REGISTRY.get(key)
            if not meta: continue
            try: parsed[key] = validate_setting_value(meta, raw)
            except ValueError as exc: errors[key] = str(exc)
        if errors: raise SettingsValidationError(errors)
        return parsed
    def rows_for_admin(self, db: Session, role: str, permission_checker) -> list[dict[str, Any]]:
        db_rows = {r.key: r for r in db.scalars(select(AppSetting).where(AppSetting.key.in_(SETTING_REGISTRY.keys()))).all()}
        rows=[]
        for key, meta in SETTING_REGISTRY.items():
            if not permission_checker(role, meta.required_permission) and role not in {"support", "viewer"}: continue
            row = db_rows.get(key)
            raw = row.value if row else meta.default
            state = "default" if raw == meta.default else ("customized" if row else "inherited/fallback")
            rows.append({"meta": meta, "key": key, "value": mask_value(raw, meta), "raw_value": "" if meta.sensitive else raw, "state": state, "can_edit": permission_checker(role, meta.required_permission), "value_type": meta.type, "description": meta.description})
        return rows
