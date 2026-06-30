import os
import sys
from pathlib import Path
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/moones_check.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.proactive_policy import (
    ProactiveCandidate,
    proactive_allowed_for_recent_user_messages,
    should_send_proactive,
    validate_proactive_text,
)
from app.api.telegram import TelegramUpdate


def test_validator_rejects_bad():
    bad = [
        "خبر خاصی" + " نیست.",
        "یه " + "تکه از حال امروزمو آروم نگه داشتم؛ بی" + "‌برچسب و بی" + "‌عجله.",
        "داشتم یه لیست" + " آهنگ جدید درست می‌کردم. تو چه خبر؟",
        "یادم افتاد به اون چیزی که گفتی.",
        "دلم برات تنگ شد.",
    ]
    for text in bad:
        ok, reason = validate_proactive_text(text, is_reply_followup=False)
        assert not ok, (text, reason)


def test_validator_accepts_good():
    good = [
        "امروز چطور پیش رفت؟",
        "سرت شلوغه؟",
        "الان وقت حرف زدن داری؟",
        "یه سر زدم ببینم هستی یا نه.",
    ]
    for text in good:
        ok, reason = validate_proactive_text(text, is_reply_followup=False)
        assert ok, (text, reason)


def test_context_reference_requires_reply_target():
    candidate = ProactiveCandidate(text="اون بازارچه چطور شد؟", kind="specific_reply_followup", source_message_id=None)
    assert should_send_proactive(candidate) is False


def test_context_reference_allowed_with_reply_target():
    candidate = ProactiveCandidate(text="اون بازارچه چطور شد؟", kind="specific_reply_followup", source_message_id=123, reply_to_telegram_message_id=123)
    assert should_send_proactive(candidate) is True


def test_user_annoyance_cooldown():
    recent = ["چی میگی", "کصخلی؟"]
    assert proactive_allowed_for_recent_user_messages(recent) is False


def test_telegram_voice_update_parsing():
    update = TelegramUpdate.model_validate({
        "update_id": 1,
        "message": {
            "message_id": 123,
            "from": {"id": 42, "first_name": "A"},
            "chat": {"id": 42},
            "voice": {"file_id": "voice-file", "duration": 7},
        },
    })
    assert update.message.voice.file_id == "voice-file"
    assert update.message.voice.duration == 7


if __name__ == "__main__":
    test_validator_rejects_bad()
    test_validator_accepts_good()
    test_context_reference_requires_reply_target()
    test_context_reference_allowed_with_reply_target()
    test_user_annoyance_cooldown()
    test_telegram_voice_update_parsing()
    print("voice_and_proactive checks passed")
