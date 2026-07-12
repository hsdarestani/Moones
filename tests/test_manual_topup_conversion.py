from app.services.coin_formatting_service import toman_to_coins, RoundingPolicy


def test_receipt_toman_converts_to_coins():
    assert toman_to_coins(100000, RoundingPolicy.FLOOR) == 1000
    assert toman_to_coins(500000, RoundingPolicy.FLOOR) == 5000
    assert toman_to_coins(1000000, RoundingPolicy.FLOOR) == 10000
