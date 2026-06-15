from __future__ import annotations

from app.engine.profile_answer_handler import profile_answer


def fast_response(message: str, situation: dict[str, object], partner_profile: dict[str, object]) -> str | None:
    text = (message or "").replace("ي", "ی").replace("ك", "ک")
    intent = str(situation.get("intent") or "")
    profile = profile_answer(intent, partner_profile)
    if profile: return _persona(profile, partner_profile)
    if intent == "greeting": return _persona("سلام :) خوبی؟", partner_profile)
    if intent == "casual_checkin":
        if "بد نیستم" in text: return _persona("خوبه که خیلی بد نیستی. امروزت چطور گذشت؟", partner_profile)
        if "چه خبر" in text: return _persona("خبر خاصی نیست، تو چه خبر؟", partner_profile)
        return _persona("من خوبم، تو چطوری؟", partner_profile)
    if intent == "clarification": return "هیچی، بد گفتم. تو بگو :)"
    if intent == "casual_life_update":
        return "عه چه خوب. چی دیدی؟" if any(w in text for w in ["سینما", "فیلم"]) else "جدی؟ تعریف کن ببینم چطور بود."
    if intent == "bot_complaint" and "اذیتم نمیکنه" in text.replace("نمی کنه", "نمیکنه"): return "آها، پس من اشتباه گرفتم."
    if intent == "bot_complaint": return "حق داری، بد گفتم. ساده‌تر می‌گم."
    if intent == "ask_comfort": return "آره عزیزم. بیا یه لحظه آروم‌ترش کنیم؛ همین که داری میگی یعنی تنها نگهش نداشتی."
    return None


def _persona(text: str, profile: dict[str, object]) -> str:
    persona = str(profile.get("personality_type") or profile.get("personality") or "")
    if "play" in persona or "شوخ" in persona: return text.replace("؟", "؟ یکم هم شیطنتت سر جاشه؟", 1) if text.endswith("؟") else text
    if "romantic" in persona or "رمانتیک" in persona: return text.replace("عزیزم", "عزیز دلم")
    return text
