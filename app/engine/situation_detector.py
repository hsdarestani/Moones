from __future__ import annotations

from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class Situation:
    intent: str
    severity: float
    entities: list[str]
    needs: list[str]


KEYWORDS: dict[str, list[str]] = {
    "financial_stress": ["چک", "چک برگشتی", "برگشت", "برگشتی", "حقوق", "حقوقمو", "پول", "قسط", "بدهی", "طلب", "وام"],
    "legal_or_banking_problem": ["حساب", "حسابام", "مسدود", "بسته", "بانک", "شکایت", "دادگاه", "قانونی", "اجراییه"],
    "emotional_distress": ["دلم گرفته", "حالم بده", "خسته شدم", "استرس", "استرس دارم", "نگران", "ترسیدم", "قفل کردم", "اذیتم", "ناراحتم"],
    "loneliness": ["تنها", "تنهایی", "هیچکس", "هیچ‌کس", "بی کسم", "بی‌کسم"],
    "bot_complaint": ["بد حرف", "بد جواب", "چرت", "نفهمیدی", "رباتی", "تکراری", "مزخرف", "اشتباه گرفتی"],
    "ask_partner_name": ["اسمت", "اسم تو", "تو کی هستی", "کی هستی", "خودتو معرفی"],
    "ask_partner_gender": ["جنسیتت", "دختری", "پسری", "زنی", "مردی"],
    "ask_partner_age": ["چند سالته", "سنت", "سن تو"],
    "ask_partner_profile": ["کدوم شهری", "اهل کجایی", "کجایی هستی", "کجا زندگی", "از کجایی"],
    "relationship_talk": ["دوستت دارم", "رابطه", "عاشق", "دوستم داری", "دلبر", "پارتنر"],
    "greeting": ["سلام", "درود", "صبح بخیر", "شب بخیر"],
    "casual_checkin": ["خوبی", "چطوری", "چه طوری", "مرسی تو چطوری", "ممنون تو چطوری"],
    "clarification": ["چی", "ها", "یعنی چی"],
    "casual_life_update": ["سینما", "فیلم", "رفتم", "رفته بودم", "امروز رفتم", "امروز رفته بودم"],
}

NEGATIVE_OR_CRISIS_KEYWORDS = KEYWORDS["financial_stress"] + KEYWORDS["legal_or_banking_problem"] + KEYWORDS["emotional_distress"] + KEYWORDS["loneliness"]
DISTRESS_INTENTS = {"emotional_distress", "financial_stress", "legal_or_banking_problem", "loneliness"}
SIMPLE_INTENTS = {"greeting", "casual_checkin", "clarification", "ask_partner_name", "ask_partner_gender", "ask_partner_age", "ask_partner_profile", "casual_life_update", "bot_complaint"}

ENTITY_PATTERNS = [
    "چک برگشتی", "چک", "حقوق", "حساب", "حسابام", "مسدود", "بسته", "بانک", "بدهی", "قسط", "خانواده", "کار", "بیماری", "جدایی"
]


def detect_situation(message: str, recent_user_messages: list[str] | None = None) -> dict[str, object]:
    """Fast deterministic situation routing; never calls an LLM."""
    text = _normalize(message)
    context = _normalize(" ".join((recent_user_messages or [])[-5:]))
    combined = f"{context} {text}".strip()
    intent = _detect_intent(text, combined)
    entities = _extract_entities(combined if intent in {"financial_stress", "legal_or_banking_problem"} else text)
    severity = _severity(intent, combined)
    needs = _needs(intent, bool(entities), bool(context))
    return asdict(Situation(intent=intent, severity=severity, entities=entities, needs=needs))


def is_real_distress(situation: dict[str, object], message: str) -> bool:
    intent = str(situation.get("intent") or "")
    text = _normalize(message)
    return intent in DISTRESS_INTENTS and _has_any(text, NEGATIVE_OR_CRISIS_KEYWORDS)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ي", "ی").replace("ك", "ک")).strip()


def _has_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def _detect_intent(text: str, combined: str) -> str:
    compact = text.replace("نمی کنه", "نمیکنه")
    if "چیزی اذیتم نمیکنه" in compact or "هیچی اذیتم نمیکنه" in compact:
        return "bot_complaint"
    if text in KEYWORDS["clarification"]:
        return "clarification"
    if text in KEYWORDS["greeting"]:
        return "greeting"
    if _has_any(text, KEYWORDS["casual_checkin"]):
        return "casual_checkin"
    if _has_any(text, KEYWORDS["bot_complaint"]):
        return "bot_complaint"
    if _has_any(text, KEYWORDS["ask_partner_name"]):
        return "ask_partner_name"
    if _has_any(text, KEYWORDS["ask_partner_gender"]):
        return "ask_partner_gender"
    if _has_any(text, KEYWORDS["ask_partner_age"]):
        return "ask_partner_age"
    if _has_any(text, KEYWORDS["ask_partner_profile"]):
        return "ask_partner_profile"
    if _has_any(combined, KEYWORDS["legal_or_banking_problem"]):
        return "legal_or_banking_problem"
    if _has_any(combined, KEYWORDS["financial_stress"]):
        return "financial_stress"
    if _has_any(text, KEYWORDS["emotional_distress"]):
        return "emotional_distress"
    if _has_any(text, KEYWORDS["loneliness"]):
        return "loneliness"
    if _has_any(text, KEYWORDS["relationship_talk"]):
        return "relationship_talk"
    if _has_any(text, KEYWORDS["casual_life_update"]):
        return "casual_life_update"
    return "casual_chat"


def _extract_entities(text: str) -> list[str]:
    found: list[str] = []
    for item in ENTITY_PATTERNS:
        if item in text and item not in found:
            found.append(item)
    return found


def _severity(intent: str, text: str) -> float:
    base = {
        "legal_or_banking_problem": 0.9,
        "financial_stress": 0.8,
        "emotional_distress": 0.65,
        "loneliness": 0.55,
        "bot_complaint": 0.35,
    }.get(intent, 0.2)
    if any(word in text for word in ["مسدود", "بسته", "برگشتی", "برگشت", "دادگاه", "شکایت"]):
        base += 0.1
    return min(base, 1.0)


def _needs(intent: str, has_entities: bool, has_context: bool) -> list[str]:
    if intent in DISTRESS_INTENTS:
        needs = ["empathy", "grounding", "specific_followup"]
    elif intent == "bot_complaint":
        needs = ["repair", "clarity"]
    elif intent.startswith("ask_partner_"):
        needs = ["direct_answer"]
    else:
        needs = ["natural_reply"]
    if has_entities and "specific_followup" not in needs:
        needs.append("specific_followup")
    if has_context and "context" not in needs:
        needs.append("context")
    return needs
