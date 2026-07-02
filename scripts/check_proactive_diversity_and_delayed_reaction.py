#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.proactive_policy import validate_proactive_text
from app.services.delayed_reaction_service import DelayedReactionService


def main() -> None:
    recent = ["امروز چطور پیش رفت؟"]
    ok, reason = validate_proactive_text("امروز چطور پیش رفت؟", is_reply_followup=False, recent_texts=recent)
    assert not ok
    assert reason == "proactive_repeated"

    bad = [
        "یکم فکرهام رو مرتب‌تر کردم؛ چیز شلوغی نبود.",
        "یه کار کوچیک کردم: چند تا چیز ریز رو مرتب کردم و بعد آروم‌تر شدم.",
        "اتفاق بزرگ نه، ولی یه تغییر کوچیک داشتم؛ چند دقیقه ذهنم رو مرتب کردم.",
        "خبر خاصی نیست.",
    ]
    for t in bad:
        ok, reason = validate_proactive_text(t, is_reply_followup=False, recent_texts=[])
        assert not ok, (t, reason)

    good = [
        "سرت شلوغه؟",
        "امروز حالت چطوره؟",
        "الان وقت حرف زدن داری؟",
        "یه سر زدم ببینم هستی یا نه.",
    ]
    for t in good:
        ok, reason = validate_proactive_text(t, is_reply_followup=False, recent_texts=[])
        assert ok, (t, reason)

    svc = DelayedReactionService()
    for t in ["کصخلی؟", "چی میگی", "چرت نگو", "/start"]:
        allowed, reason, delay = svc.should_delay_user_reply(None, t, [])
        assert not allowed, (t, reason, delay)

    allowed, reason, delay = svc.should_delay_user_reply(None, "سلام دوباره", [], force_probability=True)
    assert allowed, (reason, delay)
    assert delay is not None
    print("proactive diversity and delayed reaction checks passed")


if __name__ == "__main__":
    main()
