from app.services.telegram_service import _safe_body


def test_safe_body_redacts_bot_tokens():
    body = 'error at https://api.telegram.org/bot123456:ABC_secret/sendMessage with bot987:XYZ'
    safe = _safe_body(body)
    assert '123456:ABC_secret' not in safe
    assert '987:XYZ' not in safe
    assert 'bot<redacted>' in safe
