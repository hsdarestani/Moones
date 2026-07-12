import pytest
from app.services.settings_service import SETTING_REGISTRY, SettingsService, mask_value, validate_setting_value, serialize_setting_value


def test_invalid_values_are_rejected():
    with pytest.raises(ValueError): validate_setting_value(SETTING_REGISTRY["billing.signup_bonus_coins"], "12.4")
    with pytest.raises(ValueError): validate_setting_value(SETTING_REGISTRY["billing.signup_bonus_coins"], "-1")
    with pytest.raises(ValueError): validate_setting_value(SETTING_REGISTRY["payment.link"], "not-url")
    with pytest.raises(ValueError): validate_setting_value(SETTING_REGISTRY["llm.venice.model"], "unknown-model")
    with pytest.raises(ValueError): validate_setting_value(SETTING_REGISTRY["stickers.probability"], "1.5")


def test_valid_values_retain_types_and_serialization():
    assert validate_setting_value(SETTING_REGISTRY["billing.signup_bonus_coins"], "42") == 42
    assert serialize_setting_value(SETTING_REGISTRY["generated_media.forward_enabled"], True) == "true"
    assert str(validate_setting_value(SETTING_REGISTRY["billing.usd_to_toman"], "61000.5")) == "61000.5"


def test_sensitive_masking():
    meta = SETTING_REGISTRY["billing.usd_to_toman"]
    assert mask_value("123", meta) == "123"


def test_batch_validation_is_atomic():
    svc = SettingsService()
    with pytest.raises(Exception):
        svc.validate_changes({"billing.signup_bonus_coins":"100", "payment.link":"bad"})
