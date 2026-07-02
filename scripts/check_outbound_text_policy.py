#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.outbound_text_policy import sanitize_user_facing_text

bad = [
    "یکم فکرهام رو مرتب‌تر کردم؛ چیز شلوغی نبود.",
    "یه کار کوچیک کردم: چند تا چیز ریز رو مرتب کردم و بعد آروم‌تر شدم.",
    "اتفاق بزرگ نه، ولی یه تغییر کوچیک داشتم؛ چند دقیقه ذهنم رو مرتب کردم.",
]

for text in bad:
    cleaned, issues = sanitize_user_facing_text(text, surface="chat", user_text="چخبر")
    assert cleaned != text
    assert issues
    assert "فکرهام رو مرتب" not in cleaned
    assert "چند تا چیز ریز" not in cleaned
    assert "اتفاق بزرگ نه" not in cleaned

cleaned, issues = sanitize_user_facing_text(
    "یکم فکرهام رو مرتب‌تر کردم؛ چیز شلوغی نبود.",
    surface="proactive",
    user_text=None,
)
assert cleaned in {
    "سرت شلوغه؟",
    "امروزت چطور بود؟",
    "الان وقت حرف زدن داری؟",
    "یه سر زدم ببینم هستی یا نه.",
}
assert issues

good = "چیز خاصی نیست. تو چه خبر؟"
cleaned, issues = sanitize_user_facing_text(good, surface="chat", user_text="چخبر")
assert cleaned == good
assert not issues

print("outbound text policy checks passed")
