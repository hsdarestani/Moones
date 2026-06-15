from __future__ import annotations

PERSIAN_MODEL = "zai-org-glm-5-1"
ROLEPLAY_MODEL = "venice-uncensored-role-play"


def detect_language(text: str) -> str:
    persian = sum(1 for ch in text or "" if "\u0600" <= ch <= "\u06ff")
    latin = sum(1 for ch in text or "" if ("a" <= ch.lower() <= "z"))
    return "fa" if persian >= latin else "en"


def detect_intent(text: str, emotion: str | None = None) -> str:
    t = (text or "").lower()
    if any(x in t for x in ["roleplay", "pretend", "سناریو", "نقش بازی", "رول پلی"]):
        return "roleplay"
    if any(x in t for x in ["سلام", "hi", "hello", "hey"]):
        return "greeting"
    if any(x in t for x in ["دلم گرفته", "دلگیر", "ناراحت", "غمگین"]):
        return "sad"
    if any(x in t for x in ["کدوم شهر", "کجایی", "شهری", "محله"]):
        return "city"
    if any(x in t for x in ["حرف بزن", "باهام حرف", "talk to me"]):
        return "talk"
    if any(x in t for x in ["بد گفتی", "بد حرف", "نفهمیدم", "ربات"]):
        return "complaint"
    return emotion or "chat"


def select_model(
    user_message: str,
    detected_language: str,
    relationship_stage: str | None = None,
    intent: str | None = None,
    *,
    previous_output_quality_failed: bool = False,
    allow_persian_uncensored_roleplay: bool = False,
    primary_persian_model: str = PERSIAN_MODEL,
    roleplay_model: str = ROLEPLAY_MODEL,
) -> str:
    if previous_output_quality_failed:
        return primary_persian_model
    if detected_language == "fa":
        if intent == "roleplay" and allow_persian_uncensored_roleplay:
            return roleplay_model
        return primary_persian_model
    if detected_language == "en" and intent == "roleplay":
        return roleplay_model
    return primary_persian_model
