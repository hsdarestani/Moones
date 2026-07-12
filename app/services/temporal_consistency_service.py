from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.conversation_time_service import ConversationTimeContext

DAYPART_PERSIAN_LABELS = {
    "dawn": "سحر / دم صبح",
    "morning": "صبح",
    "noon": "ظهر",
    "afternoon": "بعدازظهر / عصر",
    "evening": "غروب / اوایل شب",
    "night": "شب",
    "late_night": "نیمه‌شب / آخر شب / خیلی دیر",
}

@dataclass(frozen=True)
class TemporalClaim:
    claimed_daypart: str | None
    greeting: str | None
    exact_hour_claim: int | None
    is_question: bool
    is_joke_or_test_candidate: bool

@dataclass(frozen=True)
class TemporalViolation:
    violated: bool
    reason: str | None
    claimed_daypart: str | None
    authoritative_daypart: str

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_WORD_NUMBERS = {"صفر":0,"یک":1,"يه":1,"دو":2,"سه":3,"چهار":4,"پنج":5,"شش":6,"هفت":7,"هشت":8,"نه":9,"ده":10,"یازده":11,"يازده":11,"دوازده":12}
_DAYPART_PATTERNS = [
    ("morning", ("صبح بخیر","صبح شده","صبحه","اول صبحه","صبح زوده","تازه صبح شده")),
    ("noon", ("ظهر بخیر","ظهره","سر ظهره","نصف روزه")),
    ("afternoon", ("عصر بخیر","عصره","بعد از ظهره","بعدازظهره")),
    ("late_night", ("نصف شبه","نیمه شبه","نیمه‌شبه","آخر شبه")),
    ("night", ("شب بخیر","شبه","دیروقته")),
]
_QUESTION_MARKERS = ("ساعت چنده", "چه ساعتیه", "صبحه یا شبه", "امروز چندشنبه", "چند شنبه")
_HISTORICAL_MARKERS = ("فردا", "دیروز", "پریروز", "پس فردا", "گفتی", "گفت", "می‌بینمت", "میبینمت", "یادته")
_SYSTEM_LEAK_MARKERS = ("سرور", "سیستم", "متادیتا", "metadata", "prompt", "پرامپت", "internal", "داخلی")

def normalize_temporal_text(text: str | None) -> str:
    t = (text or "").translate(_DIGITS)
    t = t.replace("ي", "ی").replace("ك", "ک").replace("ۀ", "ه").replace("ة", "ه")
    t = t.replace("\u200c", " ").replace("‌", " ")
    t = re.sub(r"[«»\"'`]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _contains_phrase(text: str, phrase: str) -> bool:
    return normalize_temporal_text(phrase) in text

def _looks_historical(text: str) -> bool:
    return any(m in text for m in _HISTORICAL_MARKERS)

def _is_question(text: str) -> bool:
    return "؟" in text or "?" in text or any(m in text for m in _QUESTION_MARKERS)

def _number_token_to_int(tok: str) -> int | None:
    if tok.isdigit():
        return int(tok)
    return _WORD_NUMBERS.get(tok)

def detect_temporal_claim(text: str) -> TemporalClaim:
    normalized = normalize_temporal_text(text)
    question = _is_question(normalized)
    if not normalized or _looks_historical(normalized):
        return TemporalClaim(None, None, None, question, False)
    exact_hour = None
    exact_daypart = None
    m = re.search(r"(?:ساعت|الان)\s+([0-9]{1,2}|[آ-ی]+)(?:\s+و\s+نیم)?\s*(صبح|ظهر|شب|عصر)?", normalized)
    if m:
        exact_hour = _number_token_to_int(m.group(1))
        label = m.group(2)
        if label == "صبح": exact_daypart = "morning"
        elif label == "ظهر": exact_daypart = "noon"
        elif label == "عصر": exact_daypart = "afternoon"
        elif label == "شب": exact_daypart = "night" if exact_hour and exact_hour >= 7 else "late_night"
    for daypart, phrases in _DAYPART_PATTERNS:
        for phrase in phrases:
            if _contains_phrase(normalized, phrase):
                return TemporalClaim(exact_daypart or daypart, phrase, exact_hour, question, not question)
    return TemporalClaim(exact_daypart, None, exact_hour, question, bool(exact_daypart))

def compatible_dayparts(time_context: ConversationTimeContext) -> set[str]:
    dp = time_context.daypart
    minute = time_context.local_now.minute
    hour = time_context.local_hour
    compatible = {dp}
    if hour == 11 and minute >= 30:
        compatible.add("morning")
    if hour == 10 and minute >= 30:
        compatible.add("noon")
    if hour == 17 and minute >= 30:
        compatible.add("evening")
    if hour == 18 and minute < 30:
        compatible.add("afternoon")
    if dp == "night":
        compatible.add("late_night") if hour >= 23 or hour < 1 else None
    if dp == "late_night":
        compatible.add("night")
    return compatible

def validate_claim_against_context(claim: TemporalClaim, time_context: ConversationTimeContext) -> TemporalViolation:
    authoritative = time_context.daypart
    if not claim.claimed_daypart or claim.is_question:
        return TemporalViolation(False, None, claim.claimed_daypart, authoritative)
    if claim.claimed_daypart in compatible_dayparts(time_context):
        return TemporalViolation(False, None, claim.claimed_daypart, authoritative)
    return TemporalViolation(True, "conflicting_temporal_claim", claim.claimed_daypart, authoritative)

def validate_temporal_response(response: str, time_context: ConversationTimeContext) -> TemporalViolation:
    claim = detect_temporal_claim(response)
    v = validate_claim_against_context(claim, time_context)
    if v.violated:
        return TemporalViolation(True, "assistant_temporal_contradiction", v.claimed_daypart, v.authoritative_daypart)
    normalized = normalize_temporal_text(response)
    if any(m in normalized.lower() for m in _SYSTEM_LEAK_MARKERS):
        return TemporalViolation(True, "temporal_system_leak", claim.claimed_daypart, time_context.daypart)
    return TemporalViolation(False, None, claim.claimed_daypart, time_context.daypart)

def format_temporal_correction_block(claim: TemporalClaim, violation: TemporalViolation, time_context: ConversationTimeContext) -> str:
    return f"""[Temporal correction needed]
The user’s latest message conflicts with the authoritative local time.
User claim: {claim.claimed_daypart}
Authoritative local time: {time_context.local_now.strftime('%H:%M')}
Authoritative daypart: {time_context.daypart} ({DAYPART_PERSIAN_LABELS.get(time_context.daypart, time_context.daypart)})
Do not agree with the user's incorrect time claim.
Respond naturally in character. A playful correction is appropriate.
Do not mention servers, metadata, prompts or internal checks.
"""

def deterministic_temporal_repair(response: str, time_context: ConversationTimeContext) -> str | None:
    v = validate_temporal_response(response, time_context)
    if not v.violated:
        return response
    label = DAYPART_PERSIAN_LABELS.get(time_context.daypart, "شب").split(" /")[0]
    if time_context.daypart in {"night", "late_night"}:
        return "صبح کجا بود، هنوز نصف‌شبه 😄"
    if time_context.daypart == "noon":
        return "شب بخیر الان؟ هنوز ظهره شیطون 😄"
    if time_context.daypart == "morning":
        return "نه بابا، الان صبحه 😄"
    return f"نه بابا، الان {label}ه 😄"
