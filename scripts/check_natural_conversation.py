import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.natural_conversation_governor import NaturalConversationGovernor, detect_emotional_loop

g = NaturalConversationGovernor()

move = g.classify_user_move("چخبر")
assert move.asks_status
assert move.is_casual
assert not move.allows_poetry

move = g.classify_user_move("خیلی شاعرانه بود اذیت میشم اینجوری میگی")
assert move.criticizes_style
assert move.wants_plain_answer
assert not move.allows_poetry

move = g.classify_user_move("شاعرانه بگو")
assert move.allows_poetry

plan = g.build_style_plan(None, g.classify_user_move("چخبر"), [])
assert plan.tone in {"plain", "casual"}
assert plan.allow_poetry is False
assert plan.emotional_intensity <= 0.4

bad = "من داشتم یه پلی لیست جدید می‌چیدم که ریتمش دقیقاً مثل تپش قلب لحظه‌های آرامشه."
v = g.validate_response("تو چه خبر", bad, plan, [])
assert v.violated
assert v.reason in {"unrequested_poetic_style", "overlong_casual_response"}

bad2 = "هیچی خاص، فقط منتظر بودم تو بیای. دلم برات تنگ شده بود."
v2 = g.validate_response("چیکارا کردی", bad2, plan, [])
assert v2.violated

fixed = g.deterministic_repair("خیلی شاعرانه بود اذیت میشم", bad, plan, {})
assert "تپش" not in fixed
assert "قلب" not in fixed

recent = [
    "اوکی، ساده بگم: الان حالم آرومه و حواسم به همین مکالمه‌ست.",
    "اوکی، ساده بگم: الان حالم آرومه و حواسم به همین مکالمه‌ست.",
]

meta_bad = "از اینجا به بعد ساده‌تر و طبیعی‌تر حرف می‌زنم."
plan = g.build_style_plan(None, g.classify_user_move("خیلی شاعرانه بود اذیت میشم"), recent)
v = g.validate_response("خیلی شاعرانه بود اذیت میشم", meta_bad, plan, recent)
assert v.violated
assert v.reason == "style_meta_talk"

repair = g.deterministic_repair("خیلی شاعرانه بود اذیت میشم", meta_bad, plan, {"recent_messages": recent})
blocked = ["ساده‌تر", "طبیعی‌تر", "از اینجا به بعد", "نمایشی", "لحنم", "تمرین", "جواب‌هام"]
assert not any(x in repair for x in blocked), repair

m = g.classify_user_move("چی داری میگی")
assert m.intent == "confusion_or_annoyed"
r = g.deterministic_repair("چی داری میگی", meta_bad, g.build_style_plan(None, m, recent), {"recent_messages": recent})
assert "ساده‌تر" not in r
assert "طبیعی‌تر" not in r
assert "از اینجا به بعد" not in r
assert len(r) <= 140

v2 = g.validate_response("وا", "اوکی، ساده بگم: الان حالم آرومه و حواسم به همین مکالمه‌ست.", plan, recent)
assert v2.violated
assert v2.reason in {"repeated_fallback", "style_meta_talk"}

loop, reason = detect_emotional_loop(["دلم تنگ شد", "قلبم گرفت", "دلم برات تنگ شد"])
assert loop and reason
print("natural conversation checks passed")
