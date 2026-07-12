from app.core.admin_security import hash_password, verify_password, ROLE_PERMISSIONS
from app.core.config import Settings


def test_valid_password_verifies():
    ok, _ = verify_password(hash_password("long-safe-password"), "long-safe-password")
    assert ok


def test_invalid_password_fails_safely():
    ok, _ = verify_password(hash_password("long-safe-password"), "wrong-password")
    assert not ok


def test_basic_fallback_disabled_by_default():
    assert Settings().admin_basic_fallback_enabled is False
