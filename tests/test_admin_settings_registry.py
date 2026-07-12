from app.services.settings_service import DEFAULT_SETTINGS, SETTING_CATEGORIES, SETTING_REGISTRY


def test_every_editable_setting_has_metadata():
    assert set(DEFAULT_SETTINGS) == set(SETTING_REGISTRY)
    for key, meta in SETTING_REGISTRY.items():
        assert meta.key == key
        assert meta.label
        assert meta.description
        assert meta.category in SETTING_CATEGORIES
        assert meta.type in {"boolean", "integer", "decimal", "string", "enum", "telegram_chat_id", "url", "json"}
        assert meta.required_permission


def test_legacy_subscription_label_is_clear():
    assert "اشتراک‌های قدیمی" in SETTING_CATEGORIES
    assert SETTING_REGISTRY["subscription.mini.price_coins"].category == "اشتراک‌های قدیمی"
