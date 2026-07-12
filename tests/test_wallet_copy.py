from types import SimpleNamespace

from app.services import bot_menu_service
from app.services.bot_menu_service import BotMenuService, MAIN_MENU_MARKUP
from app.services.coin_formatting_service import TOMAN_PER_COIN, toman_to_coins, RoundingPolicy
from app.services.pricing_transparency_service import PricingEstimate


class FakeWalletSvc:
    def get_or_create_wallet(self, db, user):
        return SimpleNamespace(balance_coins=1234)


class FakeSettings:
    def get_bool(self, db, key, default=False):
        return False

    def get_str(self, db, key, default=None):
        return "https://pay.example/moones"


class FakeSubscriptions:
    def get_active_subscription(self, db, user):
        return None


class FakePricingTransparencyService:
    def estimates(self, db):
        return [
            PricingEstimate("chat_short", "فرستادن یک پیام کوتاه", 11, "۱۱ سکه"),
            PricingEstimate("stt_30s", "فرستادن یک وویس ۳۰ ثانیه‌ای", 22, "۲۲ سکه"),
            PricingEstimate("stt_60s", "فرستادن یک وویس یک‌دقیقه‌ای", 33, "۳۳ سکه"),
            PricingEstimate("vision_input", "فرستادن یک عکس برای مونس", 44, "۴۴ سکه"),
            PricingEstimate("tts_100", "گرفتن یک جواب صوتی کوتاه", 55, "۵۵ سکه"),
            PricingEstimate("tts_300", "گرفتن یک جواب صوتی بلندتر", 66, "۶۶ سکه"),
        ]

    def image_bundle_estimate(self, db):
        return PricingEstimate("image_bundle", "ساخت یک عکس توسط مونس", 777, "۷۷۷ سکه")


def menu_service(monkeypatch):
    monkeypatch.setattr(bot_menu_service, "PricingTransparencyService", FakePricingTransparencyService)
    svc = BotMenuService()
    svc.wallets = FakeWalletSvc()
    svc.settings = FakeSettings()
    svc.subscriptions = FakeSubscriptions()
    return svc


def test_wallet_page_simple_copy_and_dynamic_image_price(monkeypatch):
    text = menu_service(monkeypatch).subscription_plans(None, object())
    assert "مونس مثل یک کیف پول شارژی کار می‌کنه" in text
    assert "هر ۱ سکه = ۱۰۰ تومان" in text
    assert "دریافت یک عکس از مونس: حدود ۷۷۷ سکه" in text
    assert "موجودی شما:\n۱،۲۳۴ سکه" in text


def test_wallet_page_does_not_show_balance_as_toman_equation(monkeypatch):
    text = menu_service(monkeypatch).subscription_plans(None, object())
    assert "۱،۲۳۴ سکه =" not in text
    assert "= ۱۲۳٬۴۰۰ تومان" not in text


def test_user_facing_wallet_and_topup_output_avoid_technical_terms(monkeypatch):
    svc = menu_service(monkeypatch)
    output = "\n".join([
        svc.subscription_plans(None, object()),
        svc.topup_text(None),
        " ".join(button[0]["text"] for button in MAIN_MENU_MARKUP["keyboard"] if button),
    ])
    forbidden = ["نویسه", "بینایی", "تبدیل گفتار", "ورودی/خروجی", "سیاست گرد کردن", "provider", "prompt", "token"]
    assert not any(term in output for term in forbidden)


def test_old_menu_label_remains_alias_and_new_label_opens_wallet(monkeypatch):
    svc = menu_service(monkeypatch)
    old_text, _, old_handled = svc.handle_menu_text(None, object(), "سکه‌ها و تجربه کامل‌تر")
    new_text, _, new_handled = svc.handle_menu_text(None, object(), "کیف پول و هزینه‌ها")
    assert old_handled is True
    assert new_handled is True
    assert "کیف پول مونس" in old_text
    assert "کیف پول مونس" in new_text


def test_no_billing_or_coin_calculation_changes_introduced():
    assert TOMAN_PER_COIN == 100
    assert toman_to_coins(100000, RoundingPolicy.FLOOR) == 1000
    source = open("app/services/pricing_transparency_service.py", encoding="utf-8").read()
    assert "quote_tokens(db, model='qwen-3-6-plus', input_tokens=250, output_tokens=250)" in source
    assert "image_generation_quote(db)" in source
