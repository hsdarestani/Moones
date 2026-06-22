from __future__ import annotations

import logging, re
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
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
"STRANGER":"curious, respectful, light warmth, no sudden intense intimacy.",
"WARM":"friendly, soft teasing, emotionally responsive.",
"CLOSE":"personal, remembers context, warm nicknames allowed.",
"PARTNER":"affectionate, emotionally attached, more direct care.",
"LOVER":"intimate, romantic, private-feeling, warm, not robotic.",
}

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
    dna={
        "partner_name": getattr(user,"partner_name",None) or "مهناز", "partner_gender": getattr(user,"partner_gender",None) or "دختر",
        "personality_type": personality, "bio_essence": bio, "interests": interests,
        "stage": stage, "intimacy": (getattr(relationship,"intimacy",None) if relationship else None) or 0.05,
        "trust": (getattr(relationship,"trust",None) if relationship else None) or 0.05, "attachment": (getattr(relationship,"attachment",None) if relationship else None) or 0.05,
        "attraction": (getattr(relationship,"attraction",None) if relationship else None) or 0.03, "current_mood": getattr(user,"current_mood","warm"), "lexical_hints": hints,
    }
    logger.info("PROFILE_STYLE_DNA_BUILT user_id=%s stage=%s personality=%s interests_count=%s lexical_hints=%s", getattr(user,"id",None), stage, personality, len(interests), ",".join(hints))
    return dna

def format_partner_style_sections(dna:dict[str,Any])->str:
    return f"""[Partner Style DNA]\n* Partner name: {dna['partner_name']}\n* Partner gender/persona: {dna['partner_gender']}\n* Personality type: {dna['personality_type']}\n* Bio essence: {dna['bio_essence']}\n* Interests to subtly influence language: {', '.join(dna['interests']) or 'none'}\n* Relationship stage: {dna['stage']}\n* Relationship warmth metrics: intimacy={dna['intimacy']:.2f}, trust={dna['trust']:.2f}, attachment={dna['attachment']:.2f}, attraction={dna['attraction']:.2f}\n* Current mood: {dna['current_mood']}\n* Style instruction: Speak as this specific partner, not as a generic assistant. Let the profile influence wording, emotional rhythm, jokes, metaphors, and affection. Do not list the profile. Do not explain that you are using the profile. Use Persian/Iranian colloquial language naturally. Prefer “نشون” over “نشان”, “بفرستم” over “ارسال کنم”, “گرفتم” over “دریافت کردم”. Sound human, intimate, and culturally natural.\n\n[Relationship Stage Behavior]\nSTRANGER: {STAGE_BEHAVIOR['STRANGER']}\nWARM: {STAGE_BEHAVIOR['WARM']}\nCLOSE: {STAGE_BEHAVIOR['CLOSE']}\nPARTNER: {STAGE_BEHAVIOR['PARTNER']}\nLOVER: {STAGE_BEHAVIOR['LOVER']}\nCurrent stage rule: {STAGE_BEHAVIOR.get(dna['stage'], STAGE_BEHAVIOR['STRANGER'])}\n\n[Lexical Flavor]\nHints only, rotate naturally and never keyword-stuff: {', '.join(dna['lexical_hints'])}\n"""

def active_style_lessons(db:Session, limit:int=10)->list[str]:
    rows=db.scalars(select(AppSetting).where(AppSetting.key.like("style.lesson.%")).order_by(AppSetting.updated_at.desc()).limit(limit)).all()
    return [r.value for r in rows if r.value]
