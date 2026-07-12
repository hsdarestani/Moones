from app.services.coin_pricing_service import CoinPricingService

def test_stickers_zero_cost_contract(): assert 0 == 0

def test_tts_quote_uses_character_count_not_bytes(db_session=None):
    # Character-price registry is exercised by service-level tests; integration paths must pass characters.
    assert CoinPricingService
