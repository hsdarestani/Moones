from __future__ import annotations

import logging
import os
import random
import re

logger = logging.getLogger(__name__)


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_PERSIAN_VARIANTS = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "أ": "ا", "إ": "ا"})

BAD_SELF_STATUS_PATTERNS = [
    "فکرهام رو مرتب",
    "فکرام رو مرتب",
    "ذهنم رو مرتب",
    "چند تا چیز ریز",
    "چیز شلوغی نبود",
    "یه کار کوچیک کردم",
    "اتفاق بزرگ نه",
    "یه تغییر کوچیک داشتم",
    "آروم‌تر شدم",
    "ساکت‌تر بودم",
    "یه کم آروم بودم",
    "تو حال خودم بودم",
    "چند دقیقه ذهنم",
    "چند دقیقه با خودم",
]

BAD_ABSTRACT_PATTERNS = [
    "یه تکه از حال",
    "بی‌برچسب",
    "بی‌عجله",
    "ته ذهنم",
    "سکوت",
    "تپش",
    "حس کوچیک",
]

PROACTIVE_REPLACEMENTS = [
    "سرت شلوغه؟",
    "امروزت چطور بود؟",
    "الان وقت حرف زدن داری؟",
    "یه سر زدم ببینم هستی یا نه.",
]

CHAT_WHATS_UP_REPLACEMENTS = [
    "چیز خاصی نیست. تو چه خبر؟",
    "همه‌چی آرومه. تو چطوری؟",
]

CHAT_DOING_REPLACEMENTS = [
    "داشتم یه کار روزمره می‌کردم؛ تو چه خبر؟",
    "یه کم مشغول حال‌وهوای خودم بودم، الان بگو ببینم.",
    "داشتم روزم رو می‌گذروندم. تو بگو.",
]

PLAIN_REPLACEMENTS = [
    "چیز خاصی نیست. تو چه خبر؟",
    "همینجام. بگو.",
    "الان دارم با تو حرف می‌زنم.",
]

_WHATS_UP_RE = re.compile(r"(چخبر|چه\s*خبر|چه\s*خبرا)", re.I)
_DOING_RE = re.compile(r"(چیکار(?:ا)?\s*(?:می\s?کنی|می\s?کردی|کردی)|چیکارا\s*(?:می\s?کنی|می\s?کردی|کردی)|چی\s*کار\s*(?:می\s?کنی|می\s?کردی|کردی))", re.I)


def _norm(text: str) -> str:
    text = (text or "").translate(_PERSIAN_VARIANTS).replace("\u200c", " ").lower()
    text = re.sub(r"[ًٌٍَُِّـ]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains_any(text: str, patterns: list[str]) -> list[str]:
    n = _norm(text)
    return [p for p in patterns if _norm(p) in n]


def _pick(pool: list[str], seed_text: str) -> str:
    if not pool:
        return ""
    return pool[sum(ord(ch) for ch in (seed_text or "")) % len(pool)]


def _routine_replacement(roleplay_context: dict | None) -> str | None:
    slot = (roleplay_context or {}).get("current_routine_slot") or {}
    if not slot.get("activity"):
        return None
    detail = (slot.get("shareable_detail") or "").strip()
    return (f"داشتم {slot.get('activity')}؛ {detail}" if detail else f"داشتم {slot.get('activity')}.")

def _replacement(surface: str, user_text: str | None, original: str, roleplay_context: dict | None = None) -> str:
    user = _norm(user_text or "")
    if surface == "proactive":
        return _pick(PROACTIVE_REPLACEMENTS, original)
    if _DOING_RE.search(user):
        return _routine_replacement(roleplay_context) or _pick(CHAT_DOING_REPLACEMENTS, original)
    if _WHATS_UP_RE.search(user):
        return _pick(CHAT_WHATS_UP_REPLACEMENTS, original)
    if surface in {"afterthought", "interjection", "delayed_reaction"}:
        return ""
    return _pick(PLAIN_REPLACEMENTS, original)


def sanitize_user_facing_text(text: str, *, surface: str, user_text: str | None = None, roleplay_context: dict | None = None) -> tuple[str, list[str]]:
    """Lightweight outbound guard for known fake self-status/abstract phrases."""
    if not _env_enabled("OUTBOUND_TEXT_POLICY_ENABLED", False):
        logger.info("OUTBOUND_TEXT_POLICY_SKIPPED mode=disabled")
        return text or "", []
    original = (text or "").strip()
    if not original:
        return "", ["empty"] if surface == "proactive" else []

    issues: list[str] = []
    self_hits = _contains_any(original, BAD_SELF_STATUS_PATTERNS)
    abstract_hits = _contains_any(original, BAD_ABSTRACT_PATTERNS)
    if self_hits:
        issues.extend(f"bad_self_status:{hit}" for hit in self_hits)
    if abstract_hits:
        issues.extend(f"bad_abstract:{hit}" for hit in abstract_hits)
    if not issues:
        return original, []
    return _replacement(surface, user_text, original, roleplay_context), issues
