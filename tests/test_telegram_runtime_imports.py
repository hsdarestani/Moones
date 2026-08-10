import re


def test_telegram_response_sanitizer_has_re_module():
    from app.api import telegram

    assert telegram.re is re
