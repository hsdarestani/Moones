from __future__ import annotations


def profile_answer(intent: str, partner_profile: dict[str, object]) -> str | None:
    name = str(partner_profile.get("name") or "آذر")
    age = partner_profile.get("age_range") or partner_profile.get("age") or "همون بازه‌ای که خودت برام انتخاب کردی"
    gender = partner_profile.get("gender") or partner_profile.get("partner_gender") or "پارتنر"
    city = partner_profile.get("city") or partner_profile.get("hometown")
    if intent == "ask_partner_name": return f"من {name}م :)"
    if intent == "ask_partner_age": return f"منو حدود {age} تصور کن."
    if intent == "ask_partner_gender": return f"من همون {gender}ی‌ام که با سلیقه تو ساخته شدم."
    if intent == "ask_partner_city":
        return f"منو از {city} تصور کن." if city else "شهر ثابتی ندارم؛ بیشتر با چیزایی که تو ازم ساختی شکل می‌گیرم."
    if intent == "ask_partner_profile": return f"من {name}م، همون پارتنری که خودت ساختی."
    return None
