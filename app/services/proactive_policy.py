from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re


@dataclass
class ProactiveCandidate:
    text: str
    kind: str
    source_message_id: int | None = None
    source_message_text: str | None = None
    reply_to_telegram_message_id: int | None = None
    confidence: float = 0.0


NO_CONTEXT_CHECKINS = [
    "سرت شلوغه؟",
    "امروز حالت چطوره؟",
    "الان وقت حرف زدن داری؟",
    "امروزت چطور بود؟",
    "یه سر زدم ببینم هستی یا نه.",
    "چند دقیقه وقت داری حرف بزنیم؟",
    "امروز خیلی درگیر بودی؟",
    "همه‌چی اوکیه؟",
    "کجایی این روزا؟",
    "حوصله داری یه کم گپ بزنیم؟",
]

STATUS_ANSWERS = ("خبر خاصی" + " نیست", "اتفاق خاصی نه", "اتفاق بزرگ" + " نه", "سلامتی")
SELF_STATUS = ("فکرهام رو مرتب", "فکرام رو مرتب", "چیز شلوغی نبود", "یه کار کوچیک" + " کردم", "چند تا چیز" + " ریز", "یه تغییر کوچیک داشتم", "تو حال خودم بودم", "ساکت‌تر بودم", "یه کم آروم بودم")
FAKE_INNER_LIFE = ("ذهنم رو مرتب" + " کردم", "آروم‌تر شدم", "داشتم یه لیست آهنگ", "داشتم آهنگ گوش")
BANNED_POETIC = ("یه تکه", "یه تیکه", "بی‌برچسب", "بی برچسب", "بی‌عجله", "بی عجله", "ته ذهنم", "سکوت", "تپش", "دلم", "منتظر")
TOO_ABSTRACT = ("بی‌صدا", "بی صدا", "نامعلوم", "درونم", "روحم", "حس کوچیک")
NEEDY = ("دلم برات تنگ", "داشتم بهت فکر می‌کردم", "داشتم بهت فکر میکردم", "فقط خواستم بگم هستم", "منتظرت")
CONTEXT_REFS = ("اون", "همونی که گفتی", "یادم افتاد", "بازارچه", "اون کار", "مسیر", "رانندگی", "جلسه", "سفارش", "کاری که گفتی")
ANNOYED = ("کصخلی", "چی میگی", "چی داری میگی", "وا", "چرت نگو", "نفهمیدم", "مسخره", "شاعرانه", "نمایشی", "رباتی")


def normalize_persian_text(text: str) -> str:
    text = (text or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    text = re.sub(r"[!?؟.،,؛:؛\-ـ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _norm(text: str) -> str:
    return normalize_persian_text(text)


def proactive_similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    aset, bset = set(na.split()), set(nb.split())
    jaccard = len(aset & bset) / max(1, len(aset | bset))
    return max(seq, jaccard)


def is_repeated_proactive(text: str, recent_texts: list[str]) -> bool:
    nt = _norm(text)
    if not nt:
        return True
    for recent in recent_texts or []:
        nr = _norm(recent)
        if nt == nr or proactive_similarity(nt, nr) >= 0.82:
            return True
    return False


def choose_proactive_variant(kind: str, recent_texts: list[str]) -> str | None:
    pool = NO_CONTEXT_CHECKINS
    if kind == "light_presence":
        pool = ["یه سر زدم ببینم هستی یا نه.", "الان وقت حرف زدن داری؟", "حوصله داری یه کم گپ بزنیم؟", "چند دقیقه وقت داری حرف بزنیم؟"] + NO_CONTEXT_CHECKINS
    elif kind == "simple_checkin":
        pool = NO_CONTEXT_CHECKINS
    for text in pool:
        ok, _ = validate_proactive_text(text, is_reply_followup=False, recent_texts=recent_texts)
        if ok:
            return text
    return None


def references_context(text: str) -> bool:
    t = _norm(text)
    return any(_norm(p) in t for p in CONTEXT_REFS)


def validate_proactive_text(text: str, *, is_reply_followup: bool, recent_texts: list[str] | None = None) -> tuple[bool, str | None]:
    t = _norm(text)
    if not t or len(text.strip()) > 120:
        return False, "proactive_too_abstract"
    if recent_texts and is_repeated_proactive(text, recent_texts):
        return False, "proactive_repeated"
    if any(_norm(p) in t for p in STATUS_ANSWERS):
        return False, "proactive_status_answer"
    if any(_norm(p) in t for p in SELF_STATUS):
        return False, "proactive_self_status"
    if any(_norm(p) in t for p in FAKE_INNER_LIFE):
        return False, "proactive_fake_inner_life"
    if any(_norm(p) in t for p in BANNED_POETIC):
        return False, "proactive_poetic"
    if any(_norm(p) in t for p in TOO_ABSTRACT) or any(_norm(p) in t for p in NEEDY):
        return False, "proactive_too_abstract"
    if references_context(t) and not is_reply_followup:
        return False, "proactive_contextless_reference"
    return True, None


def should_send_proactive(candidate: ProactiveCandidate, recent_texts: list[str] | None = None) -> bool:
    is_reply = candidate.kind == "specific_reply_followup" or bool(candidate.reply_to_telegram_message_id)
    ok, _ = validate_proactive_text(candidate.text, is_reply_followup=is_reply, recent_texts=recent_texts)
    if not ok:
        return False
    if references_context(candidate.text) or candidate.kind == "specific_reply_followup":
        return bool(candidate.reply_to_telegram_message_id or candidate.source_message_id)
    return True


def proactive_allowed_for_recent_user_messages(recent: list[str]) -> bool:
    joined = _norm(" ".join(recent[-5:]))
    return not any(_norm(p) in joined for p in ANNOYED)
