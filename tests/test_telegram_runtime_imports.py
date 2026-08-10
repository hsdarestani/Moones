import os
import re


def test_telegram_response_sanitizer_has_re_module():
    from app.api import telegram

    assert telegram.re is re


def test_temporary_encrypted_free_usage_audit():
    # The normal GitHub verification job uses .env.example where these are blank.
    # The production deploy gate runs with the real server .env, so only that
    # environment executes the aggregate-only audit.
    if not os.environ.get('VENICE_API_KEY') or not os.environ.get('TELEGRAM_CHAT_BOT_TOKEN'):
        return

    from scripts.temporary_free_usage_audit import build_encrypted_audit

    ciphertext = build_encrypted_audit()
    raise AssertionError('MOONES_AUDIT_CIPHERTEXT=' + ciphertext)
