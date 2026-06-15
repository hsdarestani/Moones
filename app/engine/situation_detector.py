from __future__ import annotations

import re

SUPPORTED_INTENTS = {
    "greeting", "casual_checkin", "clarification", "casual_life_update",
    "ask_partner_name", "ask_partner_age", "ask_partner_gender", "ask_partner_city", "ask_partner_profile",
    "emotional_distress", "financial_stress", "legal_or_banking_problem", "loneliness", "ask_comfort",
    "bot_complaint", "self_harm_signal", "romantic_talk", "unknown",
}
SIMPLE_INTENTS = {"greeting", "casual_checkin", "clarification", "casual_life_update", "bot_complaint", "ask_comfort"}
PROFILE_INTENTS = {"ask_partner_name", "ask_partner_age", "ask_partner_gender", "ask_partner_city", "ask_partner_profile"}
DISTRESS_INTENTS = {"emotional_distress", "financial_stress", "legal_or_banking_problem", "loneliness"}
CONTEXT_RESET_INTENTS = {"greeting", "casual_checkin", "casual_life_update", "clarification", "ask_partner_name", "ask_partner_city", "bot_complaint"}
CONTINUATION_INDICATORS = ["اون", "همون", "تبعاتش", "ادامش", "بابتش", "از همون", "هنوز", "بازم"]

KEYWORDS = {
    "self_harm_signal": ["خودمو بکشم", "خودم رو بکشم", "خودمو میکشم", "خودم رو میکشم", "میخوام بمیرم", "می خوام بمیرم", "میخوام خودمو بکشم"],
    "legal_or_banking_problem": ["حسابم بسته", "حسابام بسته", "حسابم مسدود", "حسابام مسدود", "مسدود شدن حساب", "حساب بسته", "حساب", "مسدود", "بانک", "دادگاه", "شکایت", "اجراییه"],
    "financial_stress": ["چک برگشتی", "چک", "برگشتی", "پول ندارم", "پول", "قسط", "بدهی", "وام", "حقوق"],
    "emotional_distress": ["دلم گرفته", "حالم بده", "خسته شدم", "استرس دارم", "نگرانم", "ناراحتم", "اذیتم", "ترسیدم"],
    "loneliness": ["تنها", "تنهایی", "هیچکس", "هیچ‌کس", "بی کسم", "بی‌کسم"],
    "ask_comfort": ["دلداری", "آرومم کن", "آرومم میکنی", "دلداریم بده"],
    "bot_complaint": ["چرا بد حرف", "بد حرف", "بد جواب", "چرت", "نفهمیدی", "رباتی", "تکراری", "مزخرف", "اشتباه گرفتی"],
    "ask_partner_name": ["اسمت", "اسم تو", "اسمته"],
    "ask_partner_age": ["چند سالته", "سنت", "سن تو"],
    "ask_partner_gender": ["جنسیتت", "دختر هستی", "دختری", "پسر هستی", "پسری", "زنی", "مردی"],
    "ask_partner_city": ["از کجایی", "اهل کجایی", "کدوم شهری", "کجا زندگی"],
    "ask_partner_profile": ["تو کی هستی", "کی هستی", "خودتو معرفی", "هویتت"],
    "romantic_talk": ["دوستت دارم", "عاشق", "دوستم داری", "دلبر", "بوس", "بغلم"],
    "casual_life_update": ["امروز رفتم", "رفتم سینما", "فیلم دیدم", "سینما", "فیلم", "رفته بودم"],
}
ENTITY_PATTERNS = ["چک برگشتی", "چک", "حقوق", "حساب", "حسابام", "حسابم", "مسدود", "بسته", "بانک", "بدهی", "قسط"]


def detect_situation(message: str, recent_user_messages: list[str] | None = None) -> dict[str, object]:
    text = _normalize(message)
    context = _normalize(" ".join((recent_user_messages or [])[-5:]))
    has_continuation = _has_any(text, CONTINUATION_INDICATORS)
    current_intent = _detect_current_intent(text)
    context_should_reset = current_intent in CONTEXT_RESET_INTENTS and not has_continuation
    intent = current_intent
    if current_intent == "unknown" and has_continuation:
        intent = _detect_context_intent(context) or "unknown"
    elif current_intent == "unknown" and _related_to_previous(text, context):
        intent = _detect_context_intent(context) or "unknown"
    all_text = f"{context} {text}" if has_continuation and not context_should_reset else text
    entities = _extract_entities(all_text)
    return {
        "intent": intent,
        "severity": _severity(intent, all_text),
        "confidence": _confidence(intent, text, has_continuation),
        "entities": entities,
        "needs": _needs(intent, bool(entities), has_continuation),
        "context_should_reset": context_should_reset,
    }


def is_real_distress(situation: dict[str, object], message: str) -> bool:
    return str(situation.get("intent") or "") in DISTRESS_INTENTS and _has_any(_normalize(message), sum((KEYWORDS[i] for i in DISTRESS_INTENTS), []))


def _detect_current_intent(text: str) -> str:
    compact = text.replace("نمی کنه", "نمیکنه")
    if "چیزی اذیتم نمیکنه" in compact or "هیچی اذیتم نمیکنه" in compact: return "bot_complaint"
    if any(w in compact for w in KEYWORDS["self_harm_signal"]): return "self_harm_signal"
    if compact in {"چی", "ها", "یعنی چی"}: return "clarification"
    if compact in {"سلام", "درود", "صبح بخیر", "شب بخیر"}: return "greeting"
    if "سلام" in compact and any(w in compact for w in ["چطوری", "خوبی"]): return "greeting"
    if compact in {"خوبی", "چه خبر", "بد نیستم"} or any(w in compact for w in ["چطوری", "مرسی تو چطوری", "ممنون تو چطوری"]): return "casual_checkin"
    for intent in ["bot_complaint", "ask_partner_name", "ask_partner_age", "ask_partner_gender", "ask_partner_city", "ask_partner_profile", "ask_comfort", "legal_or_banking_problem", "financial_stress", "emotional_distress", "loneliness", "romantic_talk", "casual_life_update"]:
        if _has_any(compact, KEYWORDS[intent]): return intent
    return "unknown"


def _detect_context_intent(context: str) -> str | None:
    for intent in ["legal_or_banking_problem", "financial_stress", "emotional_distress", "loneliness"]:
        if _has_any(context, KEYWORDS[intent]): return intent
    return None


def _related_to_previous(text: str, context: str) -> bool:
    return bool(text and context and set(text.split()) & set(context.split()))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ي", "ی").replace("ك", "ک")).strip()

def _has_any(text: str, words: list[str]) -> bool: return any(word in text for word in words)
def _extract_entities(text: str) -> list[str]: return [e for e in ENTITY_PATTERNS if e in text]
def _severity(intent: str, text: str) -> float:
    return {"self_harm_signal": 1.0, "legal_or_banking_problem": .9, "financial_stress": .8, "emotional_distress": .65, "loneliness": .55, "bot_complaint": .35}.get(intent, .2)
def _confidence(intent: str, text: str, continuation: bool) -> float:
    return .45 if intent == "unknown" else (.82 if continuation else .92)
def _needs(intent: str, has_entities: bool, has_context: bool) -> list[str]:
    if intent == "self_harm_signal": needs = ["safety_check", "crisis_support"]
    elif intent in DISTRESS_INTENTS: needs = ["empathy", "grounding", "specific_followup"]
    elif intent == "ask_comfort": needs = ["comfort", "grounding"]
    elif intent == "bot_complaint": needs = ["repair", "clarity"]
    elif intent.startswith("ask_partner_"): needs = ["direct_answer"]
    else: needs = ["natural_reply"]
    if has_entities and "specific_followup" not in needs: needs.append("specific_followup")
    if has_context and "context" not in needs: needs.append("context")
    return needs
