import re
from typing import Any

PERSIAN_EMOTIONAL_RE = re.compile(r"(حالم خوب نیست|دلم گرفته|ناراحت|گریه|تنها|اضطراب|استرس|غمگین|خسته|داغون)")


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 2)


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


def _has(items: list[str], *needles: str) -> bool:
    joined = " ".join(_norm(item) for item in items)
    return any(needle.lower() in joined for needle in needles)


def generate_voice_profile(
    partner_profile: dict[str, Any] | None,
    relationship_state: dict[str, Any] | Any | None,
    memory: dict[str, Any] | list[Any] | None = None,
    user_message: str = "",
) -> dict[str, Any]:
    profile = partner_profile or {}
    interests = profile.get("interests") or []
    if isinstance(interests, str):
        interests = [interests]

    voice = {
        "formality": 0.28,
        "playfulness": 0.42,
        "warmth": 0.68,
        "romance": 0.32,
        "humor": 0.35,
        "depth": 0.45,
        "emoji_probability": 0.22,
        "sentence_length": "medium",
        "slang_level": 0.25,
        "question_frequency": 0.35,
    }

    personality = _norm(profile.get("personality_type"))
    if any(x in personality for x in ("calm", "caring", "آروم", "مهربون")):
        voice.update(warmth=0.84, humor=0.25, playfulness=0.28, emoji_probability=0.16, sentence_length="medium")
    elif any(x in personality for x in ("playful", "funny", "شوخ", "بازیگوش")):
        voice.update(humor=0.78, playfulness=0.82, warmth=0.66, emoji_probability=0.34, sentence_length="short", slang_level=0.42)
    elif any(x in personality for x in ("deep", "reflective", "عمیق", "فکر")):
        voice.update(depth=0.82, humor=0.16, playfulness=0.20, emoji_probability=0.09, sentence_length="medium", slang_level=0.12, question_frequency=0.50)
    elif any(x in personality for x in ("romantic", "emotional", "رمانتیک", "احساسی")):
        voice.update(warmth=0.86, romance=0.78, depth=0.55, emoji_probability=0.28, sentence_length="medium")

    age = _norm(profile.get("age_range"))
    if "18" in age or "۲۰" in age or "20" in age:
        voice["playfulness"] += 0.10; voice["slang_level"] += 0.14; voice["emoji_probability"] += 0.08; voice["sentence_length"] = "short"
    elif "21" in age or "25" in age or "۲۱" in age or "۲۵" in age:
        voice["formality"] -= 0.04; voice["warmth"] += 0.03
    elif "26" in age or "30" in age or "۲۶" in age or "۳۰" in age:
        voice["playfulness"] -= 0.06; voice["emoji_probability"] -= 0.08; voice["slang_level"] -= 0.08; voice["depth"] += 0.08
    elif "+" in age or "بالای" in age:
        voice["formality"] += 0.08; voice["emoji_probability"] -= 0.12; voice["slang_level"] -= 0.14; voice["depth"] += 0.14

    stage = _norm(_state_value(relationship_state, "stage", ""))
    intimacy = float(_state_value(relationship_state, "intimacy", 0.0) or 0.0)
    trust = float(_state_value(relationship_state, "trust", 0.0) or 0.0)
    if "stranger" in stage:
        voice["formality"] += 0.16; voice["romance"] = min(voice["romance"], 0.12); voice["playfulness"] -= 0.08
    elif "familiar" in stage:
        voice["playfulness"] += 0.06; voice["slang_level"] += 0.04
    elif "friend" in stage:
        voice["warmth"] += 0.08; voice["formality"] -= 0.08
    elif "romantic" in stage:
        voice["romance"] += 0.18; voice["warmth"] += 0.08
    elif "partner" in stage:
        voice["romance"] += 0.24; voice["warmth"] += 0.10; voice["depth"] += 0.08
    voice["romance"] += min(0.12, intimacy * 0.15)
    voice["depth"] += min(0.10, trust * 0.12)

    if _has(interests, "موسیقی", "music"):
        voice["playfulness"] += 0.03
    if _has(interests, "حرف‌های عمیق", "deep", "روانشناسی"):
        voice["depth"] += 0.12; voice["question_frequency"] += 0.08
    if _has(interests, "شوخی", "humor"):
        voice["humor"] += 0.12; voice["playfulness"] += 0.08
    if _has(interests, "مشاوره", "life advice", "رشد"):
        voice["depth"] += 0.08; voice["warmth"] += 0.05

    memory_text = _memory_text(memory)
    if re.search(r"(کوتاه|مختصر|short)", memory_text):
        voice["sentence_length"] = "short"
    if re.search(r"(خودمونی|slang|عامیانه)", memory_text):
        voice["slang_level"] += 0.10; voice["formality"] -= 0.06
    if PERSIAN_EMOTIONAL_RE.search(user_message) or re.search(r"(emotional|sad|غم|اضطراب)", memory_text):
        voice["humor"] -= 0.18; voice["playfulness"] -= 0.15; voice["warmth"] += 0.14; voice["emoji_probability"] -= 0.10
    if re.search(r"(شوخی|joke|می‌خنده)", memory_text):
        voice["humor"] += 0.10

    for key, value in list(voice.items()):
        if isinstance(value, float):
            voice[key] = _clamp(value)
    return voice


def _memory_text(memory: dict[str, Any] | list[Any] | None) -> str:
    if isinstance(memory, dict):
        return " ".join(str(v) for v in memory.values())
    if isinstance(memory, list):
        return " ".join(str(getattr(item, "content", item)) for item in memory)
    return ""


def _state_value(state: dict[str, Any] | Any | None, key: str, default: Any) -> Any:
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default) if state is not None else default
