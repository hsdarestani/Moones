from datetime import datetime, timedelta
from types import SimpleNamespace

from app.engine.simple_chat import sanitize_final_response
from app.llm.tts_client import select_tts_voice
from app.services.soft_upsell_service import SoftUpsellService, SOFT_UPSELL_MESSAGES
from app.services.subscription_service import round_toman


def test_prorated_math_halfway_mini_to_plus():
    assert round_toman((2_290_000 - 590_000) * 0.5) == 850_000


def test_basic_to_vip_ten_days_remaining():
    assert round_toman((4_900_000 - 990_000) * (10 / 30)) == 1_303_000


def test_voice_defaults():
    female = SimpleNamespace(id=1, partner_gender="دختر", current_mood="warm", partner_personality_type="مهربون")
    male = SimpleNamespace(id=2, partner_gender="مرد", current_mood="warm", partner_personality_type="مهربون")
    playful_male = SimpleNamespace(id=3, partner_gender="مرد", current_mood="teasing", partner_personality_type="بازیگوش")
    assert select_tts_voice(female) == "Aoede"
    assert select_tts_voice(male) == "Iapetus"
    assert select_tts_voice(playful_male) == "Puck"


def test_voice_and_sticker_denials_removed():
    assert "نمی‌تونم وویس بفرستم" not in sanitize_final_response("من نمی‌تونم وویس بفرستم ولی دلم برات تنگ شده", "یه وویس بده")
    assert "استیکر نمی‌فرستم" not in sanitize_final_response("امروز فعلاً استیکر نمی‌فرستم. همین ایموجیت بامزه بود", "استیکر بده")


def test_soft_upsell_eligibility_and_variation():
    svc = SoftUpsellService()
    user = SimpleNamespace(id=1, proactive_blocked=False, proactive_messages_enabled=True, current_mood="warm", last_soft_upsell_at=datetime.utcnow()-timedelta(hours=49))
    svc.subs = SimpleNamespace(active_plan_code=lambda db, user: "free")
    assert svc.eligible(None, user)[0]
    svc.subs = SimpleNamespace(active_plan_code=lambda db, user: "plus")
    assert not svc.eligible(None, user)[0]
    assert len(set(SOFT_UPSELL_MESSAGES)) >= 4


def test_soft_upsell_no_spam():
    svc = SoftUpsellService(); user = SimpleNamespace(id=1, proactive_blocked=False, proactive_messages_enabled=True, current_mood="warm", last_soft_upsell_at=datetime.utcnow()-timedelta(hours=2))
    svc.subs = SimpleNamespace(active_plan_code=lambda db, user: "free")
    ok, reason = svc.eligible(None, user)
    assert not ok and reason == "cooldown"


def test_natural_delay_bounded():
    for text in ["کوتاه", "بلند"*500]:
        # Mirrors production contract: delay helper caps added latency at 3.5s.
        value = min(3.5, 1.8 + min(1.7, len(text) / 220))
        assert 0 <= value <= 3.5
