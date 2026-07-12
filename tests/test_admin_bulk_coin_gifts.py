from app.api.admin import _campaign_key, BULK_GIFT_CONFIRM_PHRASE


def test_campaign_key_and_confirmation_phrase():
    assert _campaign_key('Summer Gift') == _campaign_key('Summer Gift')
    assert BULK_GIFT_CONFIRM_PHRASE == 'هدیه به همه کاربران'
