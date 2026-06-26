import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.partner_autonomy_policy import is_autonomy_question, violates_autonomy_policy, safe_autonomous_fallback

assert is_autonomy_question("چیکارا کردی")
assert is_autonomy_question("هیچ اتفاقی برات نیفتاد؟")
assert is_autonomy_question("how was your day")
bad = "نه، جز اینکه مدام به ساعت نگاه کردم تا تو بیای. همین دیگه، دنیای من خلاصه میشه به تو 😘"
ok, reason = violates_autonomy_policy(bad)
assert ok
assert reason in {"passive_waiting_object", "dependent_worldview", "no_inner_life"}
ok, reason = violates_autonomy_policy('["business_work"]')
assert ok and reason == "internal_label_leak"
fallback = safe_autonomous_fallback(None, None, "چیکارا کردی")
assert "منتظر" not in fallback
assert "هیچی" not in fallback
assert "دنیای من خلاصه" not in fallback
print("autonomy policy checks passed")
