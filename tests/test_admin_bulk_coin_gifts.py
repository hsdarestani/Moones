import uuid
from app.api.admin import _campaign_key, BULK_GIFT_CONFIRM_PHRASE


def test_campaign_key_and_confirmation_phrase():
    first = _campaign_key('Summer Gift')
    second = _campaign_key('Summer Gift')
    assert first != second
    assert uuid.UUID(first)
    assert uuid.UUID(second)
    assert BULK_GIFT_CONFIRM_PHRASE == 'هدیه به همه کاربران'
