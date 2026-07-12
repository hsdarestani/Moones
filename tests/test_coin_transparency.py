from app.services.coin_formatting_service import TOMAN_PER_COIN, coins_to_toman, toman_to_coins, RoundingPolicy
from app.services.plan_config import get_plan_configs


def test_one_coin_equals_100_toman_and_rounding():
    assert TOMAN_PER_COIN == 100
    assert coins_to_toman(1000) == 100000
    assert toman_to_coins(100050, RoundingPolicy.FLOOR) == 1000


def test_plan_prices_not_100x_inflated():
    plans = get_plan_configs()
    assert plans['mini'].price_coins == 5900
    assert plans['basic'].price_coins == 9900
    assert plans['plus'].price_coins == 22900
    assert plans['vip'].price_coins == 49000
