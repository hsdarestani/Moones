from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class ProactiveCandidate:
    text: str
    kind: str
    source_message_id: int | None = None
    source_message_text: str | None = None
    reply_to_telegram_message_id: int | None = None
    confidence: float = 0.0


BANNED_POETIC = (
    "خبر خاصی" + " نیست", "یه " + "تکه", "یه " + "تیکه", "بی" + "‌برچسب", "بی " + "برچسب", "بی" + "‌عجله", "بی " + "عجله",
    "ته " + "ذهنم", "سکوت", "تپش", "قلب", "روحم", "دلم", "منتظر", "حس کوچیک",
)
STATUS_ANSWERS = ("سلامتی", "اتفاق خاصی نه", "یه کم آروم بودم", "خبر خاصی" + " نیست")
FAKE_PHYSICAL = ("داشتم آهنگ" + " گوش", "داشتم یه لیست" + " آهنگ", "لیست آهنگ", "قدم می", "بارون رو", "نشسته بودم")
NEEDY = ("دلم برات تنگ", "داشتم بهت فکر می‌کردم", "داشتم بهت فکر میکردم", "فقط خواستم بگم هستم", "منتظرت")
CONTEXT_REFS = (
    "اون", "همونی که گفتی", "یادم افتاد", "بازارچه", "اون کار", "مسیر", "رانندگی", "جلسه", "سفارش", "کاری که گفتی"
)
ANNOYED = ("کصخلی", "چی میگی", "چی داری میگی", "وا", "چرت نگو", "نفهمیدم", "مسخره", "شاعرانه", "نمایشی", "رباتی")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ي", "ی").replace("ك", "ک")).strip()


def references_context(text: str) -> bool:
    t = _norm(text)
    return any(p in t for p in CONTEXT_REFS)


def validate_proactive_text(text: str, *, is_reply_followup: bool) -> tuple[bool, str | None]:
    t = _norm(text)
    if not t:
        return False, "proactive_too_abstract"
    if len(t) > 120:
        return False, "proactive_too_abstract"
    if any(p in t for p in STATUS_ANSWERS) or t.startswith(("سلامتی", "اتفاق خاصی نه")):
        return False, "proactive_status_answer"
    if any(p in t for p in FAKE_PHYSICAL):
        return False, "proactive_fake_physical_claim"
    if any(p in t for p in NEEDY):
        return False, "proactive_needy"
    if any(p in t for p in BANNED_POETIC):
        return False, "proactive_poetic"
    if references_context(t) and not is_reply_followup:
        return False, "proactive_contextless_reference"
    return True, None


def should_send_proactive(candidate: ProactiveCandidate) -> bool:
    is_reply = candidate.kind == "specific_reply_followup" or bool(candidate.reply_to_telegram_message_id)
    ok, _ = validate_proactive_text(candidate.text, is_reply_followup=is_reply)
    if not ok:
        return False
    if references_context(candidate.text) or candidate.kind == "specific_reply_followup":
        return bool(candidate.reply_to_telegram_message_id or candidate.source_message_id)
    return True


def proactive_allowed_for_recent_user_messages(recent: list[str]) -> bool:
    joined = _norm(" ".join(recent[-5:]))
    return not any(p in joined for p in ANNOYED)
