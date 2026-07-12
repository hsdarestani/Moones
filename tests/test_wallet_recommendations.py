from types import SimpleNamespace
from app.services.bot_menu_service import BotMenuService

class Settings:
    def get_int(self, db, key, default): return {'wallet.recommendation.starter_coins':111,'wallet.recommendation.regular_coins':222,'wallet.recommendation.heavy_coins':333,'wallet.recommendation.default_coins':222}.get(key, default)
    def get_str(self, db, key, default=None): return 'https://pay.example'

def test_recommendations_from_settings_and_toman_derived():
    svc=BotMenuService(); svc.settings=Settings()
    text=svc._recommendation_text(None)
    assert '۱۱۱ سکه' in text and '11,100 تومان' in text
    assert 'پیشنهاد ما برای شروع معمولی: ۲۲۲ سکه' in text


def test_topup_has_recommendations_no_plan_language():
    svc=BotMenuService(); svc.settings=Settings()
    text=svc.topup_text(None)
    assert 'برای شروع چقدر شارژ کنم؟' in text
    assert 'پلن' not in text and 'اشتراک' not in text
