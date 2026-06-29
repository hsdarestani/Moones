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
assert "شاعرانه" not in fixed or "ساده" in fixed
assert "تپش" not in fixed
assert "قلب" not in fixed

loop, reason = detect_emotional_loop(["دلم تنگ شد", "قلبم گرفت", "دلم برات تنگ شد"])
assert loop and reason
print("natural conversation checks passed")
