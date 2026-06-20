import logging
import os
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.core.logger import mask_secrets
from app.llm.tts_client import select_tts_voice
from app.services.bot_menu_service import BotMenuService, MAIN_MENU_MARKUP
from app.services.onboarding_service import OnboardingService, START_TEXT
from app.services.plan_config import get_plan_configs
from app.services.credit_validation import parse_admin_credit_amount, ADMIN_CREDIT_ERROR


def test_start_and_about_copy_available():
    intro = OnboardingService().intro()
    assert "شروعش رایگانه" in START_TEXT
    assert "شروع رایگان" in str(intro.reply_markup)
    assert "مونس چیه؟" in str(intro.reply_markup)
    about = BotMenuService().about_text()
    assert "همراه هوشمند شخصیه" in about
    assert "پارتنرت" in about
    assert "مونس چیه؟" in str(MAIN_MENU_MARKUP)
    forbidden = ("token", "توکن", "سکه", "quota", "مدل")
    assert not any(x in START_TEXT + about for x in forbidden)


def test_plan_copy_is_premium_and_hides_internal_media_quotas():
    class Wallets:
        balance_coins = 0
    class FakeWalletSvc:
        def get_or_create_wallet(self, db, user):
            return Wallets()
    svc = BotMenuService(); svc.wallets = FakeWalletSvc()
    text = svc.subscription_plans(None, object())
    assert "حدود ۱۵ پیام معمولی در روز" in text
    assert "حدود ۵۰ پیام معمولی در روز" in text
    assert "حدود ۱۰۰ پیام معمولی در روز" in text
    assert "گفت‌وگوی نامحدود منصفانه" in text
    assert "گفت‌وگوی نامحدود ویژه" in text
    assert "ظرفیت‌ها تقریبی‌اند" in text
    assert "sub_activate_plus" in str(svc.subscription_keyboard())
    assert "سکه" not in text
    assert "token" not in text.lower() and "توکن" not in text
    assert "ویس در روز" not in text and "استیکر در روز" not in text


def test_internal_voice_and_sticker_quotas_remain_configured():
    cfg = get_plan_configs()
    assert cfg["free"].daily_voice_limit == 0
    assert cfg["mini"].daily_voice_limit == 1
    assert cfg["basic"].daily_sticker_limit == 15
    assert cfg["plus"].daily_sticker_limit == 60
    assert cfg["vip"].daily_sticker_limit == 120


def test_voice_selection_by_gender_and_logs(caplog):
    class U:
        id = 42
        partner_gender = "male"
        current_mood = "warm"
        partner_personality_type = "calm_caring"
    with caplog.at_level(logging.INFO):
        assert select_tts_voice(U(), {}, None, None) == "Iapetus"
    assert "TTS_VOICE_SELECTED" in caplog.text
    assert select_tts_voice(None, {"gender": "male"}, "playful", None) == "Puck"
    assert select_tts_voice(None, {"gender": "female"}, "playful", None) == "Aoede"
    assert select_tts_voice(None, {"gender": "دختر"}, "warm", None) == "Aoede"


def test_required_channel_gate_copy_and_keyboard():
    source = open("app/api/telegram.py", encoding="utf-8").read()
    assert "عضو کانال" in source
    assert "https://t.me/MoonesAI" in open("app/core/config.py", encoding="utf-8").read()
    assert "check_required_channel" in source


def test_admin_large_credit_validation_is_friendly():
    amount, error = parse_admin_credit_amount("9999999999")
    assert amount is None
    assert error == ADMIN_CREDIT_ERROR
    amount, error = parse_admin_credit_amount("-1")
    assert amount is None and error == ADMIN_CREDIT_ERROR
    assert parse_admin_credit_amount("2000")[0] == 2000


def test_secret_masking_and_quota_messages():
    raw = "POST https://api.telegram.org/bot8658672306:ABC/sendMessage DATABASE_URL=postgresql://u:p@h/db DB_PASSWORD=secret VENICE_API_KEY=vk"
    masked = mask_secrets(raw)
    assert "bot8658672306:ABC" not in masked
    assert "bot<redacted>" in masked
    assert "secret" not in masked and "vk" not in masked
    source = open("app/api/telegram.py", encoding="utf-8").read()
    for msg in ("فعلاً با متن کنارت می‌مونم", "برای حفظ کیفیت تجربه"):
        assert msg in source
    assert "سکه" not in source
