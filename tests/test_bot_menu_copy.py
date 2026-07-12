from types import SimpleNamespace
from app.services.bot_menu_service import BotMenuService
from app.models.addon import AddonProduct


def test_about_text_new_copy_has_no_plan_language():
    text = BotMenuService().about_text()
    assert 'دریافت عکس از مونس' in text
    assert 'پلن' not in text and 'provider' not in text and 'prompt' not in text


def test_addons_copy_separates_active(monkeypatch):
    svc=BotMenuService()
    products=[AddonProduct(key='image_generation_unlock', title='دریافت عکس از مونس', description='امکان درخواست و دریافت عکس از مونس رو فعال می‌کنه. هزینه هر عکس جداگانه از کیف پول کم می‌شه.', price_coins=500, is_active=True), AddonProduct(key='x', title='افزودنی دیگر', description='توضیح', price_coins=1, is_active=True)]
    svc.addons=SimpleNamespace(list_active_addons=lambda db: products, user_has_addon=lambda db, uid, key: key=='image_generation_unlock', get_addon_price_coins=lambda db,key: 500)
    user=SimpleNamespace(id=1, intimacy_override_max=False)
    text=svc.addons_text(None,user)
    assert 'اینجا می‌تونی قابلیت‌های بیشتری برای مونس فعال کنی.' in text
    assert text.count('دریافت عکس از مونس') == 1
    assert 'پلن' not in text
