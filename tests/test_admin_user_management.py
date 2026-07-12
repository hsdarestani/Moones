from app.core.admin_security import normalize_username, MIN_PASSWORD_LENGTH


def test_username_normalized():
    assert normalize_username(" Owner ") == "owner"


def test_minimum_password_length_documented():
    assert MIN_PASSWORD_LENGTH >= 12
