from types import SimpleNamespace
from app.services.bot_menu_service import BotMenuService, MAIN_MENU_MARKUP
from app.models.addon import AddonProduct


def test_about_text_describes_text_only_product():
    text = BotMenuService().about_text()
    assert 'فقط روی چت متنی' in text
    assert 'قابلیت‌های عکس و وویس موقتاً غیرفعال‌اند' in text
    assert 'پلن' not in text and 'provider' not in text and 'prompt' not in text


def test_main_menu_hides_media_addons_and_relationship_progress():
    labels = [button['text'] for row in MAIN_MENU_MARKUP['keyboard'] for button in row]
    assert '🧩 افزودنی‌ها' not in labels
    assert '🧠 وضعیت رابطه' not in labels
    assert '💬 رفتن به چت' in labels


def test_addons_copy_separates_active_in_legacy_mode(monkeypatch):
    svc=BotMenuService()
    products=[AddonProduct(key='image_generation_unlock', title='دریافت عکس از مونس', description='امکان درخواست و دریافت عکس از مونس رو فعال می‌کنه. هزینه هر عکس جداگانه از کیف پول کم می‌شه.', price_coins=500, is_active=True), AddonProduct(key='x', title='افزودنی دیگر', description='توضیح', price_coins=1, is_active=True)]
    svc.addons=SimpleNamespace(list_active_addons=lambda db: products, user_has_addon=lambda db, uid, key: key=='image_generation_unlock', user_addon_enabled=lambda db, uid, key: True, get_addon_price_coins=lambda db,key: 500)
    user=SimpleNamespace(id=1, intimacy_override_max=False)
    text=svc.addons_text(None,user)
    assert 'اینجا می‌تونی قابلیت‌های بیشتری برای مونس فعال کنی.' in text
    assert text.count('دریافت عکس از مونس') == 1
    assert 'پلن' not in text
