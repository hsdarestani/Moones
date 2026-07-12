from __future__ import annotations

import re
from typing import Any

_PERSIAN_VARIANTS = str.maketrans({"ي":"ی","ك":"ک","ۀ":"ه","ة":"ه","أ":"ا","إ":"ا","آ":"ا"})

def _norm(text: str) -> str:
    text = (text or "").translate(_PERSIAN_VARIANTS).lower().replace("\u200c", " ")
    return re.sub(r"\s+", " ", text).strip()

AUTONOMY_QUESTION_PATTERNS = [
    r"چیکار(?:ا)? کردی", r"امروز چطور گذشت", r"چه خبر", r"چخبر", r"اتفاقی افتاد",
    r"هیچ اتفاقی برات نیفتاد", r"روزت چطور بود", r"دلت چی می ?خواست", r"امروز چی فهمیدی",
    r"امروز چه حسی داشتی", r"وقتی نبودم چی شد", r"what did you do", r"how was your day",
    r"anything happen", r"what happened",
]

BANNED_DEPENDENT_PATTERNS = [
    r"منتظرت بودم", "".join(["فقط ", "منتظر بودم"]), r"همش منتظر بودم", "".join(["مدام به ساعت ", "نگاه کردم"]),
    "".join(["هیچی خاص", r"،? فقط"]), r"هیچ کاری نکردم", r"هیچ اتفاقی نیفتاد", "".join(["دنیای من ", r"خلاصه می ?شه به تو"]),
    r"بدون تو هیچ", r"فقط دلم برات تنگ شده بود", r"کاش بیای", r"کجایی پس",
    r"فقط خواستم بگم هستم", r"من فقط اینجام", r"دلم فقط پیش تو بود", r"هیچی،? فقط به تو فکر کردم",
    r"دلم پیش تو بود", r"نبودی و من",
]

_INTERNAL_LEAK_RE = re.compile(r"(\[[^\]]{1,200}\]|\{[^{}]{1,260}\}|\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b|\b(event_type|metadata|memory_key|selected_memories|system prompt|prompt text|relationship_stage)\b)", re.I)
_EMPTY_RE = re.compile(r"^(نه|نخیر|هیچی|هیچ چیز|چیز خاصی نه|نه چیز خاصی)([،,.\s]|$)")

def is_autonomy_question(text: str) -> bool:
    n = _norm(text)
    return any(re.search(p, n, re.I) for p in AUTONOMY_QUESTION_PATTERNS)

def violates_autonomy_policy(text: str) -> tuple[bool, str | None]:
    n = _norm(text)
    if not n:
        return True, "empty_answer"
    if _INTERNAL_LEAK_RE.search(text or ""):
        return True, "internal_label_leak"
    if re.search("".join(["مدام به ساعت ", r"نگاه کردم|منتظر(?:ت)? بودم|همش منتظر|فقط منتظر|نبودی و من|کاش بیای|کجایی پس"]), n):
        return True, "passive_waiting_object"
    if re.search("".join(["دنیای من ", r"خلاصه می ?شه به تو|بدون تو هیچ|دلم فقط پیش تو بود|فقط دلم برات تنگ شده بود"]), n):
        return True, "dependent_worldview"
    if _EMPTY_RE.search(n) or re.search("".join(["هیچی خاص", r"،? فقط|هیچ کاری نکردم|هیچ اتفاقی نیفتاد"]), n):
        return True, "no_inner_life"
    for pat in BANNED_DEPENDENT_PATTERNS:
        if re.search(pat, n, re.I):
            return True, "needy_dependency"
    return False, None

_FALLBACKS = [
    "چیز خاصی نیست. تو چه خبر؟",
    "همینجام. بگو.",
    "الان دارم با تو حرف می‌زنم.",
]

def safe_autonomous_fallback(user: Any, recent_life_event: Any = None, user_message: str = "", roleplay_context: dict | None = None) -> str:
    slot = (roleplay_context or {}).get("current_routine_slot") or {}
    if slot.get("activity"):
        detail = (slot.get("shareable_detail") or "").strip()
        loc = (slot.get("location") or "").strip()
        text = f"داشتم {slot.get('activity')}" + (f" توی {loc}" if loc else "") + (f"؛ {detail}" if detail else ".")
        bad, _ = violates_autonomy_policy(text)
        if not bad:
            return text
    content = (getattr(recent_life_event, "content", None) or "").strip()
    growth = (getattr(recent_life_event, "growth_note", None) or "").strip()
    if content:
        text = content
        if growth and growth not in text:
            text += f" {growth}"
    else:
        idx = (getattr(user, "id", 0) or len(user_message or "")) % len(_FALLBACKS)
        text = _FALLBACKS[idx]
    bad, _ = violates_autonomy_policy(text)
    return _FALLBACKS[0] if bad else text
