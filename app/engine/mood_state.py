from __future__ import annotations

from datetime import datetime
from typing import Any

RUDE_WORDS = {"احمق", "خفه", "گمشو", "کثافت", "عوضی", "لعنتی", "بیشعور", "fuck", "shit", "stupid"}
APOLOGY_WORDS = {"ببخش", "معذرت", "شرمنده", "sorry", "متاسفم"}
AFFECTION_WORDS = {"عزیزم", "قشنگم", "دوستت دارم", "دلم برات", "مهربونم", "عشقم", "نازم", "بوس"}
PLAYFUL_WORDS = {"شیطون", "شوخی", "هه", "خخ", "😂", "😜", "ناز نکن", "flirt"}


def _contains_any(text: str, words: set[str]) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in words)


def ensure_mood_defaults(user: Any) -> Any:
    defaults = {
        "current_mood": "warm",
        "affection_score": 0,
        "trust_score": 0,
        "irritation_score": 0,
        "playfulness_score": 0,
        "consecutive_text_count": 0,
        "consecutive_voice_count": 0,
        "consecutive_sticker_count": 0,
        "consecutive_cold_replies": 0,
        "last_mood": None,
        "last_mood_at": None,
    }
    for field, value in defaults.items():
        if getattr(user, field, None) is None:
            setattr(user, field, value)
    return user


def update_mood_from_text(user: Any, text: str) -> Any:
    ensure_mood_defaults(user)
    if _contains_any(text, RUDE_WORDS):
        user.irritation_score = min(10, int(user.irritation_score or 0) + 2)
        user.affection_score = max(-10, int(user.affection_score or 0) - 1)
        user.current_mood = "cold" if user.irritation_score >= 5 else "slightly_upset"
        user.last_rude_message_at = datetime.utcnow()
    elif _contains_any(text, APOLOGY_WORDS):
        user.irritation_score = max(0, int(user.irritation_score or 0) - 2)
        user.affection_score = min(10, int(user.affection_score or 0) + 1)
        user.current_mood = "warm"
    elif _contains_any(text, AFFECTION_WORDS):
        user.affection_score = min(10, int(user.affection_score or 0) + 1)
        user.trust_score = min(10, int(user.trust_score or 0) + 1)
        user.current_mood = "affectionate"
    elif _contains_any(text, PLAYFUL_WORDS):
        user.playfulness_score = min(10, int(user.playfulness_score or 0) + 1)
        user.current_mood = "playful"
    else:
        user.irritation_score = max(0, int(user.irritation_score or 0) - 1)
    return user
