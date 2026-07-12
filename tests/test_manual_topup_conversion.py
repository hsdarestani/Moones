from app.services.coin_formatting_service import toman_to_coins, RoundingPolicy


def test_receipt_toman_converts_to_coins():
    assert toman_to_coins(100000, RoundingPolicy.FLOOR) == 1000
    assert toman_to_coins(500000, RoundingPolicy.FLOOR) == 5000
    assert toman_to_coins(1000000, RoundingPolicy.FLOOR) == 10000


def test_topup_page_has_conversion_examples_and_no_consumption_estimates(monkeypatch):
    from app.services.bot_menu_service import BotMenuService

    class FakeSettings:
        def get_str(self, db, key, default=None):
            return "https://pay.example/moones"

    svc = BotMenuService()
    svc.settings = FakeSettings()
    text = svc.topup_text(None)
    assert "• ۱۰۰٬۰۰۰ تومان = ۱٬۰۰۰ سکه" in text
    assert "• ۵۰۰٬۰۰۰ تومان = ۵٬۰۰۰ سکه" in text
    assert "• ۱٬۰۰۰٬۰۰۰ تومان = ۱۰٬۰۰۰ سکه" in text
    assert "فرستادن یک پیام کوتاه" not in text
    assert "برآورد مصرف" not in text
    assert "هزینه‌های تقریبی" not in text
