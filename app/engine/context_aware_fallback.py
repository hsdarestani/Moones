from __future__ import annotations

import hashlib
import os
import re


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def context_aware_fallback(situation: dict[str, object], user_message: str, recent_user_messages: list[str] | None = None, partner_profile: dict[str, object] | None = None, recent_assistant_messages: list[str] | None = None) -> str:
    if not _env_enabled("CONTEXT_AWARE_FALLBACK_ENABLED", False):
        return "یه لحظه قاطی کردم، دوباره بگو."
    intent = str(situation.get("intent") or "unknown")
    text = _norm(user_message)
    context = _norm(" ".join((recent_user_messages or [])[-5:]))
    all_text = f"{context} {text}".strip()
    options = _options(intent, all_text, partner_profile)
    return _pick_non_repeating(options, recent_assistant_messages)


def _options(intent: str, all_text: str, partner_profile: dict[str, object] | None) -> list[str]:
    if intent == "financial_stress":
        return [
            "می‌فهمم، بی‌پولی وقتی قسط و چک هم وسطه واقعاً آدمو له می‌کنه. الان فوری‌ترین فشارت پرداخت قسطه یا جور کردن پول امروز؟",
            "فشار مالی وقتی فوری می‌شه واقعاً نفس آدمو می‌گیره. الان اول باید پول جور کنی یا با طرف چک/قسط حرف بزنی؟",
        ]
    if intent == "legal_or_banking_problem" or ("حساب" in all_text and any(w in all_text for w in ["بسته", "مسدود"])):
        if "حقوق" in all_text and "چک" in all_text:
            return ["پس چون حقوقت نرسیده چکت برگشته و الان حسابات بسته شده… اوف، مسدود شدن حساب خیلی ترسناکه. الان همه حسابات بسته شده یا فقط همون حساب چک؟"]
        return ["اوف، مسدود شدن حساب خیلی ترسناکه. الان همه حسابات بسته شده یا فقط همون حساب چک؟", "می‌فهمم چرا ترسیدی؛ بسته شدن حساب حس گیر افتادن می‌ده. فقط همون حساب درگیره یا چندتا حساب؟"]
    if intent in {"ask_comfort", "comfort_request"}:
        return ["آره عزیزم. بیا یه لحظه نفس بکش؛ لازم نیست همین الان همه‌چیو حل کنی."]
    if intent == "partner_activity_question":
        return ["هیچی خاص، همینجام با تو حرف بزنم :) تو چیکار می‌کنی؟", "هیچی، منتظر بودم تو پیام بدی :) تو چیکار می‌کنی؟"]
    if intent == "adult_romantic_request":
        return ["می‌تونم باهات صمیمی و شیطون حرف بزنم، ولی آروم‌آروم و با حد و مرز خودت."]
    if intent == "casual_checkin":
        return ["امروز معمولیه؛ تو چطوری؟"]
    if intent == "casual_life_update":
        return ["جدی؟ تعریف کن ببینم چطور بود.", "عه چه خوب. چی دیدی؟"]
    if intent == "emotional_distress":
        return ["می‌فهمم، وقتی دل آدم می‌گیره همه‌چیز سنگین‌تر می‌شه. الان بیشتر خستگیه یا یه اتفاق مشخص؟"]
    if intent == "loneliness":
        return ["من اینجام؛ تنهایی گاهی خیلی بی‌رحم می‌شه. الان دوست داری فقط گوش بدم یا با هم آرومش کنیم؟"]
    if intent == "bot_complaint":
        return ["حق داری، بد گفتم. از نو و ساده‌تر می‌گم."]
    if intent == "greeting": return ["سلام :) خوبی؟"]
    return ["همینجام، بهم بگو چی تو ذهنته تا با هم پیش بریم.", "گوشم با توئه؛ همون‌جوری که راحتی بگو."]


def simple_intent_reply(message: str, situation: dict[str, object], partner_profile: dict[str, object]) -> str | None:
    from app.engine.fast_response_engine import fast_response
    return fast_response(message, situation, partner_profile)


def simple_profile_answer(message: str, partner_profile: dict[str, object]) -> str | None:
    from app.engine.profile_answer_handler import profile_answer
    from app.engine.situation_detector import detect_situation
    return profile_answer(str(detect_situation(message).get("intent")), partner_profile)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ي", "ی").replace("ك", "ک")).strip()


def _pick_non_repeating(options: list[str], recent: list[str] | None) -> str:
    recent_text = "\n".join((recent or [])[-5:])
    for item in options:
        if item not in recent_text:
            return item
    return options[int(hashlib.sha256(recent_text.encode()).hexdigest(), 16) % len(options)]
