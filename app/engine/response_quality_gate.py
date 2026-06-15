from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from app.engine.persian_humanizer import humanize_persian

PERSIAN_RE = re.compile(r"[اآبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی]")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
FOREIGN_RE = re.compile(r"(?:[A-Za-z]{4,}\s+){2,}|[А-Яа-я]{2,}|[\u4e00-\u9fff]{1,}|[\uac00-\ud7af]{1,}")
EMOJI_DESCRIPTION_RE = re.compile(r"\([^)]*(?:بوسه|لبخند|قلب|چشمک|hug|kiss|smile)[^)]*\)", re.I)
BANNED = ["چه کاری", "می‌توانم", "می توانم", "مایل هستی", "بسیار", "برخوردار است", "می‌باشد", "می باشد", "در خدمتم", "کاربر عزیز", "چطور می‌توانم", "سوال دیگری"]
RECOVERY = ["یه لحظه بد جواب دادم", "اوپس، جوابم یه کم کج رفت", "حرفم قاطی شد"]

@dataclass
class QualityGateResult:
    final_text: str
    accepted: bool
    rejected: bool
    reason: str = ""


def apply_quality_gate(text: str, intent: str, recent_assistant_messages: list[str] | None = None) -> QualityGateResult:
    recent = recent_assistant_messages or []
    candidate = humanize_persian(text or "")
    reason = rejection_reason(candidate, recent)
    if not reason:
        return QualityGateResult(_dedupe_emoji(candidate, recent), True, False, "")
    rewritten = _dedupe_emoji(humanize_persian(EMOJI_DESCRIPTION_RE.sub("", candidate)), recent)
    second = rejection_reason(rewritten, recent)
    if not second:
        return QualityGateResult(rewritten, False, True, reason)
    return QualityGateResult(intent_fallback(intent, recent), False, True, f"{reason}; {second}")


def rejection_reason(text: str, recent: list[str] | None = None) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return "empty"
    if FOREIGN_RE.search(compact):
        return "foreign_fragments"
    if EMOJI_DESCRIPTION_RE.search(compact):
        return "emoji_description"
    if len(EMOJI_RE.findall(compact)) > 1:
        return "emoji_spam"
    if any(p in compact for p in BANNED):
        return "formal_or_support_phrase"
    if any(p in compact for p in RECOVERY):
        return "scripted_recovery"
    words = re.findall(r"\S+", compact)
    if any(words[i:i+3] == words[i+3:i+6] for i in range(max(0, len(words)-5))):
        return "repeated_phrase_loop"
    if not PERSIAN_RE.search(compact) and len(compact) > 12:
        return "not_persian"
    if compact in (recent or [])[-5:]:
        return "repeated_recent_response"
    return ""


def intent_fallback(intent: str, recent: list[str] | None = None) -> str:
    options = {
        "greeting": ["سلام :) خوبی؟", "سلام، خوبی؟", "سلام، چه خبر؟"],
        "sad": ["چی شده؟ می‌خوای برام بگی؟", "دلت گرفته؟ بگو ببینم چی شده.", "ای وای، چی شده؟"],
        "city": ["راستش من شهر ثابتی ندارم، بیشتر با چیزی که تو ازم می‌سازی شکل می‌گیرم.", "شهر ثابتی ندارم؛ کم‌کم با تو شکل می‌گیرم."],
        "talk": ["باشه، بیا حرف بزنیم. الان دلت چی می‌خواد بشنوی؟", "حتماً، بگو الان تو دلت چی می‌گذره؟"],
        "complaint": ["حق داری، بد گفتم. ساده‌تر می‌گم.", "درست می‌گی، اینو ساده‌تر می‌گم."],
    }.get(intent, ["بگو ببینم چی تو دلت هست؟", "یه کم ساده‌تر بگو تا بهتر باهات پیش بیام."])
    recent_text = " ".join((recent or [])[-5:])
    for item in options:
        if item not in recent_text:
            return item
    return options[int(hashlib.sha256(recent_text.encode()).hexdigest(), 16) % len(options)]


def _dedupe_emoji(text: str, recent: list[str]) -> str:
    emojis = EMOJI_RE.findall(text)
    if not emojis:
        return text.strip()
    previous = EMOJI_RE.findall(recent[-1]) if recent else []
    keep = next((e for e in emojis if not previous or e != previous[-1]), "")
    base = EMOJI_RE.sub("", text).strip()
    return f"{base} {keep}".strip() if keep else base
