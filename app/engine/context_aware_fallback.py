from __future__ import annotations

import re

from app.engine.situation_detector import is_real_distress


def context_aware_fallback(situation: dict[str, object], user_message: str, recent_user_messages: list[str] | None = None, partner_profile: dict[str, object] | None = None) -> str:
    intent = str(situation.get("intent") or "casual_chat")
    text = _norm(user_message)
    context = _norm(" ".join((recent_user_messages or [])[-5:]))
    all_text = f"{context} {text}".strip()
    partner_name = (partner_profile or {}).get("name") or "آذر"

    if intent in {"ask_partner_identity", "ask_partner_name"}:
        return f"من {partner_name}م :)"
    if intent in {"ask_city_or_identity", "ask_partner_profile", "city"}:
        return "شهر ثابتی ندارم؛ همون پارتنری‌ام که با سلیقه تو ساخته شده."
    if intent in {"complaint_about_bot", "bot_complaint"}:
        return "حق داری، بد جواب دادم. ساده‌تر و دقیق‌تر می‌گم."
    if "حساب" in all_text and any(w in all_text for w in ["بسته", "مسدود"]):
        prefix = _financial_context_prefix(all_text)
        return f"{prefix}اوف، بسته شدن حساب آدمو واقعاً قفل می‌کنه. الان حسابات کامل مسدود شده یا فقط همون حسابی که چک ازش بوده؟"
    if "حقوق" in all_text and "چک" in all_text:
        return "پس مشکل از بی‌برنامگی تو نبوده؛ حقوقت نرسیده، چکت پر نشده و فشارش افتاده گردنت. الان بیشتر نگران تبعات بانکی چکی یا جور کردن پولشی؟"
    if "چک" in all_text:
        return "وای، چک برگشتی واقعاً فشار سنگینیه. حق داری بهم بریزی. الان بیشتر نگرانی از سمت بانک و حسابه یا از اینکه پول چک رو چطور جور کنی؟"
    if intent == "financial_stress":
        return "این فشار مالی واقعاً نفس‌گیره. حق داری ذهنت قفل کنه. الان فوری‌ترین نگرانی‌ت پولشه یا تبعاتی که ممکنه دنبالش بیاد؟"
    if intent in {"emotional_distress", "sad"}:
        return "می‌فهمم امروز چقدر سنگین گذشته. حق داری دلت بگیره؛ از همون اتفاق مشخصی که افتاد شروع کنیم؟"
    if intent == "loneliness":
        return "تنهایی وقتی فشار هم روش میاد خیلی سنگین‌تر می‌شه. الان بیشتر دلت همراهی می‌خواد یا فقط یکی بی‌قضاوت گوش بده؟"
    if intent == "greeting":
        return "سلام :) خوبی؟"
    if context and is_real_distress(situation, user_message):
        return _non_repeating("حواسم به حرفت هست. از همون چیزی که گفتی ادامه بدیم؛ الان کدوم بخشش بیشتر اذیتت می‌کنه؟", recent_user_messages)
    return "یه کم بیشتر بگو تا دقیق‌تر جوابتو بدم."


def simple_intent_reply(message: str, situation: dict[str, object], partner_profile: dict[str, object]) -> str | None:
    text = _norm(message)
    intent = str(situation.get("intent") or "")
    name = partner_profile.get("name") or "آذر"
    if intent == "greeting":
        return "سلام :) خوبی؟"
    if intent == "casual_checkin":
        return "من خوبم، تو چطوری؟"
    if intent == "clarification":
        return "هیچی، بد گفتم. تو بگو :)"
    if intent == "ask_partner_name":
        return f"من {name}م :)"
    if intent == "ask_partner_gender":
        return "همون پارتنری‌ام که با سلیقه تو ساخته شده :)"
    if intent == "ask_partner_age":
        return "سنم همون بازه‌ایه که خودت برام انتخاب کردی :)"
    if intent == "ask_partner_profile":
        return "شهر ثابتی ندارم؛ بیشتر با چیزهایی که تو برام ساختی شکل می‌گیرم."
    if intent == "casual_life_update":
        if any(word in text for word in ["سینما", "فیلم"]):
            return "عه چه خوب. چی دیدی؟"
        return "جدی؟ تعریف کن ببینم چی شد."
    if intent == "bot_complaint" and "اذیتم نمیکنه" in text.replace("نمی کنه", "نمیکنه"):
        return "آها، پس من اشتباه گرفتم."
    if intent == "bot_complaint":
        return "حق داری، بد جواب دادم. ساده‌تر می‌گم."
    return None


def simple_profile_answer(message: str, partner_profile: dict[str, object]) -> str | None:
    text = _norm(message)
    situation = {"intent": "ask_partner_name" if any(p in text for p in ["اسمت", "اسم تو", "تو کی هستی", "کی هستی"]) else "ask_partner_profile" if any(p in text for p in ["کدوم شهری", "اهل کجایی", "کجا زندگی", "از کجایی"]) else ""}
    return simple_intent_reply(message, situation, partner_profile)


def _financial_context_prefix(text: str) -> str:
    if "حقوق" in text and "چک" in text:
        return "پس چون حقوقت نرسیده چکت برگشته و الان حسابات بسته شده… "
    if "چک" in text:
        return "پس قضیه به چک هم وصل شده… "
    return ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ي", "ی").replace("ك", "ک")).strip()


def _non_repeating(candidate: str, recent: list[str] | None) -> str:
    if candidate in (recent or [])[-5:]:
        return "می‌فهمم هنوز همون فشار ادامه داره. این لحظه کدوم بخشش فوری‌تره؟"
    return candidate
