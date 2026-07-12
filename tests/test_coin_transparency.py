from app.services.coin_formatting_service import TOMAN_PER_COIN, coins_to_toman, toman_to_coins, RoundingPolicy
from app.services.plan_config import get_plan_configs


def test_one_coin_equals_100_toman_and_rounding():
    assert TOMAN_PER_COIN == 100
    assert coins_to_toman(1000) == 100000
    assert toman_to_coins(100050, RoundingPolicy.FLOOR) == 1000


def test_plan_prices_not_100x_inflated():
    plans = get_plan_configs()
    assert plans['mini'].price_coins == 5900
    assert plans['basic'].price_coins == 9900
    assert plans['plus'].price_coins == 22900
    assert plans['vip'].price_coins == 49000


def test_pricing_estimate_labels_are_conversational(monkeypatch):
    from app.services.pricing_transparency_service import PricingTransparencyService

    class Quote:
        charged_coins = 1

    class FakePricing:
        def quote_tokens(self, *args, **kwargs):
            return Quote()
        def quote_unit(self, *args, **kwargs):
            return Quote()
        def quote_usd(self, *args, **kwargs):
            return Quote()

    svc = PricingTransparencyService(FakePricing())
    labels = {e.key: e.label for e in svc.estimates(None)}
    assert labels["chat_short"] == "فرستادن یک پیام کوتاه"
    assert labels["stt_30s"] == "فرستادن یک وویس ۳۰ ثانیه‌ای"
    assert labels["stt_60s"] == "فرستادن یک وویس یک‌دقیقه‌ای"
    assert labels["vision_input"] == "فرستادن یک عکس برای مونس"
    assert labels["tts_100"] == "گرفتن یک جواب صوتی کوتاه"
    assert labels["tts_300"] == "گرفتن یک جواب صوتی بلندتر"
    assert labels["image_1k"] == "ساخت یک عکس"
    assert labels["image_2k"] == "ساخت یک عکس با کیفیت بالاتر"
    assert all("نویسه" not in label and "بینایی" not in label and "تبدیل گفتار" not in label for label in labels.values())
