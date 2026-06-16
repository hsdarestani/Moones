from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

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
FINANCIAL_CONTEXT_TERMS = KEYWORDS["financial_stress"] + KEYWORDS["legal_or_banking_problem"]


def detect_situation(message: str, recent_user_messages: list[str] | None = None) -> dict[str, object]:
    text = _normalize(message)
    context_messages = (recent_user_messages or [])[-5:]
    context = _normalize(" ".join(context_messages))
    has_continuation = _has_any(text, CONTINUATION_INDICATORS)
    current_intent, matched_keywords = _detect_current_intent(text)
    context_should_reset = current_intent in CONTEXT_RESET_INTENTS and not has_continuation
    context_intent = _detect_context_intent(context)
    context_keywords = _matched_keywords(context, FINANCIAL_CONTEXT_TERMS if context_intent in {"financial_stress", "legal_or_banking_problem"} else KEYWORDS.get(context_intent or "", []))
    reason = "current_message_rule"
    context_used = False
    intent = current_intent

    if current_intent == "financial_stress" and not _has_any(text, KEYWORDS["financial_stress"]):
        intent = "unknown"
        matched_keywords = []
        reason = "financial_stress_blocked_without_current_financial_keyword"

    if intent == "unknown" and has_continuation and not context_should_reset:
        if context_intent == "financial_stress":
            if _finance_continuation_allowed(text, context):
                intent = "financial_stress"
                matched_keywords = _matched_keywords(text, FINANCIAL_CONTEXT_TERMS) or context_keywords
                reason = "continuation_with_recent_financial_context"
                context_used = True
            else:
                reason = "financial_context_ignored_no_current_finance_signal"
        elif context_intent:
            intent = context_intent
            matched_keywords = _matched_keywords(text, KEYWORDS.get(intent, [])) or context_keywords
            reason = "continuation_context_rule"
            context_used = True
    elif intent == "unknown" and _related_to_previous(text, context):
        if context_intent and context_intent != "financial_stress":
            intent = context_intent
            matched_keywords = context_keywords
            reason = "related_context_rule"
            context_used = True
        elif context_intent == "financial_stress":
            reason = "old_financial_context_not_enough_without_continuation"

    all_text = f"{context} {text}" if context_used and not context_should_reset else text
    entities = _extract_entities(all_text)
    result = {
        "intent": intent,
        "severity": _severity(intent, all_text),
        "confidence": _confidence(intent, text, context_used),
        "entities": entities,
        "needs": _needs(intent, bool(entities), context_used),
        "context_should_reset": context_should_reset,
        "matched_keywords": matched_keywords,
        "context_used": context_used,
        "context_reset": context_should_reset,
        "why": reason,
    }
    logger.info(
        "SITUATION_DETECT raw_user_message=%r detected_intent=%s confidence=%s why=%s matched_keywords=%s context_used=%s context_reset=%s",
        message,
        result["intent"],
        result["confidence"],
        reason,
        matched_keywords,
        context_used,
        context_should_reset,
    )
    return result

def is_real_distress(situation: dict[str, object], message: str) -> bool:
    return str(situation.get("intent") or "") in DISTRESS_INTENTS and _has_any(_normalize(message), sum((KEYWORDS[i] for i in DISTRESS_INTENTS), []))


def _detect_current_intent(text: str) -> tuple[str, list[str]]:
    compact = text.replace("نمی کنه", "نمیکنه")
    if "چیزی اذیتم نمیکنه" in compact or "هیچی اذیتم نمیکنه" in compact: return "bot_complaint", ["چیزی اذیتم نمیکنه"]
    self_harm = _matched_keywords(compact, KEYWORDS["self_harm_signal"])
    if self_harm: return "self_harm_signal", self_harm
    if compact in {"چی", "ها", "یعنی چی"}: return "clarification", [compact]
    if compact in {"سلام", "درود", "صبح بخیر", "شب بخیر"}: return "greeting", [compact]
    if "سلام" in compact and any(w in compact for w in ["چطوری", "خوبی"]): return "greeting", _matched_keywords(compact, ["سلام", "چطوری", "خوبی"])
    if compact in {"خوبی", "چه خبر", "بد نیستم"} or any(w in compact for w in ["چطوری", "مرسی تو چطوری", "ممنون تو چطوری"]): return "casual_checkin", _matched_keywords(compact, ["خوبی", "چه خبر", "بد نیستم", "چطوری", "مرسی تو چطوری", "ممنون تو چطوری"])
    for intent in ["bot_complaint", "ask_partner_name", "ask_partner_age", "ask_partner_gender", "ask_partner_city", "ask_partner_profile", "ask_comfort", "legal_or_banking_problem", "financial_stress", "emotional_distress", "loneliness", "romantic_talk", "casual_life_update"]:
        matched = _matched_keywords(compact, KEYWORDS[intent])
        if matched: return intent, matched
    return "unknown", []


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


def _matched_keywords(text: str, words: list[str]) -> list[str]:
    return [word for word in words if word and word in text]


def _finance_continuation_allowed(text: str, context: str) -> bool:
    return _has_any(text, FINANCIAL_CONTEXT_TERMS) or (
        _has_any(text, CONTINUATION_INDICATORS)
        and _has_any(context, FINANCIAL_CONTEXT_TERMS)
    )
