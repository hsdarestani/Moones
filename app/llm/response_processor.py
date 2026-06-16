import hashlib
import logging
import re
from typing import Any

BULLET_RE = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s*", re.MULTILINE)
PERSIAN_RE = re.compile(r"[اآبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی]")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
BANNED_PHRASES = [
    "چطور می‌توانم", "در خدمتم", "مایل هستی", "از چه محله ای", "از چه محله‌ای", "بسیار",
    "برخوردار است", "می‌باشد", "من یک هوش مصنوعی هستم", "به عنوان", "چگونه می‌توانم کمکتان کنم",
    "آیا سوال دیگری دارید", "در ادامه چند نکته", "کاربر عزیز",
]
FORMAL_REPLACEMENTS = {
    "در نتیجه": "پس",
    "مایلم بدانم": "دوست دارم بدونم",
    "امیدوارم کمک کرده باشم": "",
}
REPEATED_ENDINGS = ["من اینجام، آروم برام بگو", "من اینجام؛ آروم برام بگو چی توی دلت می‌گذره", "اگر سوال دیگری دارید، در خدمتم"]
logger = logging.getLogger(__name__)
GARBAGE_FRAGMENTS = re.compile(r"(?:[A-Za-z]{4,}\s+){4,}|[А-Яа-я]{3,}|[ぁ-ゟ゠-ヿ]{2,}|[ก-๙]{2,}")


def post_process_response(
    text: str,
    voice_profile: dict[str, Any] | None = None,
    recent_assistant_messages: list[str] | None = None,
    user_message: str = "",
    allow_fallback: bool = True,
) -> tuple[str, dict[str, bool]] | str:
    flags = {"garbage_filter_triggered": False, "repetition_filter_triggered": False}
    voice_profile = voice_profile or {}
    recent_assistant_messages = recent_assistant_messages or []
    raw = text or ""
    if is_garbage_output(raw):
        flags["garbage_filter_triggered"] = True
        if allow_fallback:
            return _fallback(voice_profile, recent_assistant_messages), flags
        return raw.strip(), flags

    cleaned = raw.strip()
    cleaned = BULLET_RE.sub("", cleaned)
    cleaned = re.sub(r"#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"`+", "", cleaned)
    for formal, natural in FORMAL_REPLACEMENTS.items():
        cleaned = cleaned.replace(formal, natural)
    for phrase in BANNED_PHRASES + REPEATED_ENDINGS:
        cleaned = cleaned.replace(phrase, "")
    lines = [line.strip(" -•\t") for line in cleaned.splitlines() if line.strip()]
    cleaned = " ".join(lines)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .،\n")
    cleaned = _limit_emojis(cleaned, voice_profile, recent_assistant_messages, user_message)
    cleaned = _limit_length(cleaned, voice_profile)

    if not cleaned and raw.strip():
        logger.warning("PROCESSOR_EMPTY_GUARD raw_len=%s user_message=%r", len(raw), user_message)
        cleaned = _light_clean(raw)
    if not cleaned or is_garbage_output(cleaned):
        flags["garbage_filter_triggered"] = True
        if allow_fallback:
            cleaned = _fallback(voice_profile, recent_assistant_messages)
        else:
            cleaned = cleaned or _light_clean(raw)
    if is_repetitive(cleaned, recent_assistant_messages):
        flags["repetition_filter_triggered"] = True
        if allow_fallback:
            cleaned = _fallback(voice_profile, recent_assistant_messages)
    return cleaned[:1200], flags


def is_garbage_output(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return True
    if not PERSIAN_RE.search(compact) and len(compact) > 20:
        return True
    if GARBAGE_FRAGMENTS.search(compact) and len(PERSIAN_RE.findall(compact)) < 12:
        return True
    words = re.findall(r"\w+", compact.lower())
    for i in range(max(0, len(words) - 5)):
        if len(set(words[i:i + 6])) == 1:
            return True
    if re.search(r"(.{3,25})\1\1", compact):
        return True
    return False


def is_repetitive(text: str, recent: list[str]) -> bool:
    ending = _ending(text)
    recent_endings = [_ending(item) for item in recent[-10:]]
    if ending and any(ending == item or ending.endswith(item) or item.endswith(ending) for item in recent_endings):
        return True
    emojis = EMOJI_RE.findall(text)
    if emojis and recent and emojis[-1:] == EMOJI_RE.findall(recent[-1])[-1:]:
        return True
    words = re.findall(r"\S+", text)
    return any(words[i:i + 3] == words[i + 3:i + 6] for i in range(max(0, len(words) - 5)))


def _ending(text: str) -> str:
    chunks = [chunk.strip() for chunk in re.split(r"[.!؟?\n]", text.strip()) if chunk.strip()]
    return (chunks[-1] if chunks else text.strip()).strip()[-45:]


def _limit_emojis(text: str, voice: dict[str, Any], recent: list[str], user_message: str) -> str:
    if re.search(r"(حالم خوب نیست|دلم گرفته|خودکشی|مرگ|گریه|اضطراب شدید)", user_message):
        return EMOJI_RE.sub("", text)
    probability = float(voice.get("emoji_probability", 0.2) or 0.0)
    emojis = EMOJI_RE.findall(text)
    if probability < 0.12:
        return EMOJI_RE.sub("", text)
    if not emojis:
        return text
    previous = EMOJI_RE.findall(recent[-1]) if recent else []
    keep = next((e for e in emojis if not previous or e != previous[-1]), "")
    text = EMOJI_RE.sub("", text).rstrip()
    return f"{text} {keep}".strip() if keep else text


def _limit_length(text: str, voice: dict[str, Any]) -> str:
    limit = {"short": 180, "medium": 360, "long": 650}.get(str(voice.get("sentence_length", "medium")), 360)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].strip() + "…"


def _fallback(voice: dict[str, Any], recent: list[str]) -> str:
    playful = float(voice.get("playfulness", 0) or 0) > 0.65
    deep = float(voice.get("depth", 0) or 0) > 0.65
    romantic = float(voice.get("romance", 0) or 0) > 0.6
    options = [
        "یه لحظه بد جواب دادم… دوباره بگو ببینم چی می‌خواستی بپرسی؟",
        "صبر کن، این یکی خوب درنیومد. دوباره ازم بپرس، جمع‌وجورش می‌کنم.",
        "حرفم قاطی شد؛ تو همون سوالتو یه بار دیگه بگو.",
    ]
    if playful:
        options.append("اوپس، جوابم یه کم کج رفت. دوباره بگو تا درستش کنم.")
    if deep:
        options.append("حس می‌کنم جواب قبلیم دقیق نبود؛ دوباره بگو تا آروم‌تر باهات پیش برم.")
    if romantic:
        options.append("حواسم پرت شد عزیزم؛ دوباره بگو، این بار نرم‌تر جواب می‌دم.")
    recent_text = " ".join(recent[-5:])
    for option in options:
        if _ending(option) not in recent_text:
            return option
    index = int(hashlib.sha256(recent_text.encode()).hexdigest(), 16) % len(options)
    return options[index]


def _light_clean(text: str) -> str:
    cleaned = BULLET_RE.sub("", text or "")
    cleaned = re.sub(r"#{1,6}\s*|`+|\*\*", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" .،\n")
