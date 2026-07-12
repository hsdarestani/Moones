from app.services.bot_link_service import management_bot_keyboard, management_bot_url
from app.services.soft_upsell_service import SoftUpsellService


def test_management_keyboard_uses_url(monkeypatch):
    monkeypatch.setattr('app.services.bot_link_service.get_settings', lambda: type('S',(),{'management_bot_username':'ConfiguredBot','telegram_management_bot_username':'','management_bot_url':''})())
    kb = management_bot_keyboard('کیف پول', start='wallet')
    btn = kb['inline_keyboard'][0][0]
    assert 'url' in btn and btn['url'] == 'https://t.me/ConfiguredBot?start=wallet'
    assert 'callback_data' not in btn


def test_soft_upsell_no_sub_back_callback(monkeypatch):
    monkeypatch.setattr('app.services.bot_link_service.get_settings', lambda: type('S',(),{'management_bot_username':'ConfiguredBot','telegram_management_bot_username':'','management_bot_url':''})())
    assert 'sub_back' not in str(SoftUpsellService().keyboard())
