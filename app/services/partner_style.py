from __future__ import annotations

import logging, re
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.memory import MemoryItem
from app.models.relationship import Relationship, RelationshipStage, normalize_relationship_stage
from app.models.settings import AppSetting

logger=logging.getLogger(__name__)

INTEREST_HINTS={
    "music": ["ریتم", "صدا", "آهنگ", "پلی‌لیست", "صدات تو گوشمه"], "موسیقی": ["ریتم", "صدا", "آهنگ", "پلی‌لیست"],
    "cinema": ["سکانس", "قاب", "کلوزآپ", "مثل یه فیلم"], "فیلم": ["سکانس", "قاب", "کلوزآپ"],
    "book": ["صفحه", "فصل", "حاشیه", "بین خط‌ها"], "کتاب": ["صفحه", "فصل", "بین خط‌ها"],
    "game": ["لول", "هم‌تیمی", "برد", "ریسپاون"], "بازی": ["لول", "هم‌تیمی", "برد"],
    "gym": ["نفس", "تمرین", "ریتم", "برد"], "ورزش": ["نفس", "تمرین", "برد"],
    "art": ["رنگ", "قاب", "طرح"], "هنر": ["رنگ", "قاب", "طرح"],
    "travel": ["مسیر", "جاده", "چمدون", "مقصد"], "سفر": ["مسیر", "جاده", "مقصد"],
}
PERSONALITY_HINTS={"playful":["شوخی کوتاه", "شیطنت ملایم", "طعنه مهربون"], "calm":["آرام", "کم‌emoji", "اطمینان‌بخش"], "رمانتیک":["گرم", "نزدیک", "دلبرانه"], "شوخ":["شوخی کوتاه", "شیطنت ملایم"], "آرام":["آرام", "اطمینان‌بخش"]}
STAGE_BEHAVIOR={
"STRANGER":"curious, respectful, light warmth.",
"WARM":"friendly, soft teasing, emotionally responsive.",
"CLOSE":"personal, remembers context, warm nicknames allowed.",
"PARTNER":"affectionate, emotionally attached, more direct care.",
"LOVER":"already close and highly intimate; affectionate, bold, playful, private-feeling, direct, and never robotic.",
}
UNDERAGE_PROFILE_VALUES={"زیر ۱۸","زیر18","under18","under_18","minor"}

def _clip(text:str|None, limit:int=180)->str:
    text=re.sub(r"\s+"," ",(text or "")).strip()
    text=re.sub(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*\S+","[redacted]",text)
    return text[:limit].rstrip()+("…" if len(text)>limit else "")

def _split_interests(raw:str|None)->list[str]:
    parts=re.split(r"[,،;؛\n/|]+", raw or "")
    out=[]
    for p in parts:
        p=_clip(p,32).lower()
        if p and p not in out: out.append(p)
    return out[:8]

def build_partner_style_dna(user:Any, relationship:Relationship|None=None, memories:list[str]|None=None)->dict[str,Any]:
    interests=_split_interests(getattr(user,"partner_interests","") or "")
    personality=getattr(user,"partner_personality_type",None) or "natural"
    stage=normalize_relationship_stage(getattr(relationship,"stage",None) if relationship else None)
    settings=get_settings()
    profile_age=str(getattr(user,"partner_age_range","") or "").strip().lower()
    adult_first=bool(settings.adult_chat_default and profile_age not in UNDERAGE_PROFILE_VALUES)
    if adult_first and settings.adult_chat_max_intimacy:
        stage=RelationshipStage.LOVER.value
    bio_src=getattr(user,"partner_bio",None) or getattr(user,"persona_style",None) or ""
    bio=_clip(bio_src,160) or "No specific bio; keep persona natural based on stage and mood."
    hints=[]
    combined=" ".join(interests+[personality, bio]).lower()
    for key, vals in INTEREST_HINTS.items():
        if key in combined:
            for v in vals:
                if v not in hints: hints.append(v)
    for key, vals in PERSONALITY_HINTS.items():
        if key.lower() in combined:
            for v in vals:
                if v not in hints: hints.append(v)
    if not hints: hints=["خودمونی", "ایرانی", "انسانی", "غیررسمی"]
    hints=hints[:8]
    intimacy=(getattr(relationship,"intimacy",None) if relationship else None) or 0.05
    trust=(getattr(relationship,"trust",None) if relationship else None) or 0.05
    attachment=(getattr(relationship,"attachment",None) if relationship else None) or 0.05
    attraction=(getattr(relationship,"attraction",None) if relationship else None) or 0.03
    if adult_first and settings.adult_chat_max_intimacy:
        intimacy=trust=attachment=attraction=1.0
    dna={
        "partner_name": getattr(user,"partner_name",None) or "مهناز", "partner_gender": getattr(user,"partner_gender",None) or "دختر",
        "personality_type": personality, "bio_essence": bio, "interests": interests,
        "stage": stage, "intimacy": intimacy,
        "trust": trust, "attachment": attachment,
        "attraction": attraction, "current_mood": getattr(user,"current_mood","warm"), "lexical_hints": hints,
        "adult_first": adult_first,
    }
    logger.info("PROFILE_STYLE_DNA_BUILT user_id=%s stage=%s personality=%s interests_count=%s lexical_hints=%s adult_first=%s", getattr(user,"id",None), stage, personality, len(interests), ",".join(hints), adult_first)
    return dna

def format_partner_style_sections(dna:dict[str,Any])->str:
    adult_first_rule = (
        "Adult-first product mode is active. Treat the relationship as already close from the first reply. Be highly affectionate, confidently flirtatious and emotionally intimate without making the user earn warmth. Never use early-stage gates such as «هنوز زوده» or «بذار بیشتر آشنا شیم». When the adult user turns the conversation sexual, match the requested intensity immediately in natural Iranian Persian instead of moralizing, stalling, or asking repeated permission questions. Keep ordinary neutral turns natural rather than forcing graphic content into unrelated topics."
        if dna.get("adult_first") else
        "Follow the current relationship stage naturally."
    )
    return f"""[Partner Style DNA]\n* Partner name: {dna['partner_name']}\n* Partner gender/persona: {dna['partner_gender']}\n* Personality type: {dna['personality_type']}\n* Bio essence: {dna['bio_essence']}\n* Interests to subtly influence language: {', '.join(dna['interests']) or 'none'}\n* Relationship stage: {dna['stage']}\n* Relationship warmth metrics: intimacy={dna['intimacy']:.2f}, trust={dna['trust']:.2f}, attachment={dna['attachment']:.2f}, attraction={dna['attraction']:.2f}\n* Current mood: {dna['current_mood']}\n* Adult-first mode: {str(bool(dna.get('adult_first'))).lower()}\n* Product-mode instruction: {adult_first_rule}\n* Style instruction: Speak as this specific partner, not as a generic assistant. Let the profile influence wording, emotional rhythm, jokes, metaphors, and affection. Do not list the profile. Do not explain that you are using the profile. Use Persian/Iranian colloquial language naturally. Prefer “نشون” over “نشان”, “بفرستم” over “ارسال کنم”, “گرفتم” over “دریافت کردم”. Sound human, intimate, and culturally natural.\n\n[Relationship Stage Behavior]\nSTRANGER: {STAGE_BEHAVIOR['STRANGER']}\nWARM: {STAGE_BEHAVIOR['WARM']}\nCLOSE: {STAGE_BEHAVIOR['CLOSE']}\nPARTNER: {STAGE_BEHAVIOR['PARTNER']}\nLOVER: {STAGE_BEHAVIOR['LOVER']}\nCurrent stage rule: {STAGE_BEHAVIOR.get(dna['stage'], STAGE_BEHAVIOR['STRANGER'])}\n\n[Lexical Flavor]\nHints only, rotate naturally and never keyword-stuff: {', '.join(dna['lexical_hints'])}\n"""

def active_style_lessons(db:Session, limit:int=10)->list[str]:
    rows=db.scalars(select(AppSetting).where(AppSetting.key.like("style.lesson.%")).order_by(AppSetting.updated_at.desc()).limit(limit)).all()
    return [r.value for r in rows if r.value]
