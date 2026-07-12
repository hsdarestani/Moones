from types import SimpleNamespace
from app.services.bot_menu_service import BotMenuService


def test_settings_single_toggle_null_enabled():
    svc=BotMenuService(); user=SimpleNamespace(proactive_messages_enabled=None)
    text=svc.settings_text(user); kb=svc.settings_keyboard(user)
    assert 'روشنه' in text
    flat=str(kb)
    assert flat.count('proactive_toggle') == 1
    assert 'proactive_on' not in flat and 'proactive_off' not in flat


def test_settings_single_toggle_false():
    svc=BotMenuService(); user=SimpleNamespace(proactive_messages_enabled=False)
    assert 'خاموشه' in svc.settings_text(user)
    assert svc.settings_keyboard(user)['inline_keyboard'][0][0]['text'] == 'روشن کردن پیام‌های خودجوش'
