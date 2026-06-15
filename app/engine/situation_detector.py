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
    "financial_stress": ["چک", "چک برگشتی", "برگشت", "حقوق", "حقوقمو", "پول", "قسط", "بدهی", "طلب", "وام"],
    "legal_or_banking_problem": ["حساب", "حسابام", "مسدود", "بسته", "بانک", "شکایت", "دادگاه", "قانونی", "اجراییه"],
    "emotional_distress": ["دلم گرفته", "حالم بده", "خسته شدم", "استرس", "نگران", "ترسیدم", "قفل کردم"],
    "loneliness": ["تنها", "تنهایی", "هیچکس", "هیچ‌کس", "بی کسم", "بی‌کسم"],
    "complaint_about_bot": ["بد حرف", "بد جواب", "چرت", "نفهمیدی", "رباتی", "تکراری", "مزخرف"],
    "ask_partner_identity": ["اسمت", "اسم تو", "تو کی هستی", "کی هستی", "خودتو معرفی"],
    "ask_city_or_identity": ["کدوم شهری", "اهل کجایی", "کجایی هستی", "کجا زندگی"],
    "relationship_talk": ["دوستت دارم", "رابطه", "عاشق", "دوستم داری", "دلبر", "پارتنر"],
    "greeting": ["سلام", "درود", "صبح بخیر", "شب بخیر"],
}

ENTITY_PATTERNS = [
    "چک برگشتی", "چک", "حقوق", "حساب", "حسابام", "مسدود", "بسته", "بانک", "بدهی", "قسط", "خانواده", "کار", "بیماری", "جدایی"
]


def detect_situation(message: str, recent_user_messages: list[str] | None = None) -> dict[str, object]:
    text = _normalize(message)
    context = _normalize(" ".join((recent_user_messages or [])[-5:]))
    combined = f"{context} {text}".strip()
    intent = _detect_intent(text, combined)
    entities = _extract_entities(combined if intent in {"financial_stress", "legal_or_banking_problem"} else text)
    severity = _severity(intent, combined)
    needs = _needs(intent, bool(entities), bool(context))
    return asdict(Situation(intent=intent, severity=severity, entities=entities, needs=needs))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ي", "ی").replace("ك", "ک")).strip()


def _has_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def _detect_intent(text: str, combined: str) -> str:
    if _has_any(text, KEYWORDS["complaint_about_bot"]):
        return "complaint_about_bot"
    if _has_any(text, KEYWORDS["ask_partner_identity"]):
        return "ask_partner_identity"
    if _has_any(text, KEYWORDS["ask_city_or_identity"]):
        return "ask_city_or_identity"
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
    if text in KEYWORDS["greeting"] or _has_any(text, KEYWORDS["greeting"]):
        return "greeting"
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
        "complaint_about_bot": 0.35,
    }.get(intent, 0.2)
    if any(word in text for word in ["مسدود", "بسته", "برگشتی", "برگشت", "دادگاه", "شکایت"]):
        base += 0.1
    return min(base, 1.0)


def _needs(intent: str, has_entities: bool, has_context: bool) -> list[str]:
    if intent in {"financial_stress", "legal_or_banking_problem", "emotional_distress", "loneliness"}:
        needs = ["empathy", "grounding", "specific_followup"]
    elif intent == "complaint_about_bot":
        needs = ["repair", "clarity"]
    elif intent in {"ask_partner_identity", "ask_city_or_identity"}:
        needs = ["direct_answer"]
    else:
        needs = ["natural_reply"]
    if has_entities and "specific_followup" not in needs:
        needs.append("specific_followup")
    if has_context and "context" not in needs:
        needs.append("context")
    return needs
