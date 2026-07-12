from __future__ import annotations
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

TOMAN_PER_COIN = 100

_PERSIAN_DIGITS = str.maketrans('0123456789,.-', '۰۱۲۳۴۵۶۷۸۹،٫-')

class RoundingPolicy:
    FLOOR = 'floor'
    CEIL = 'ceil'
    HALF_UP = 'half_up'


def coins_to_toman(coins: int | Decimal | float | str) -> int:
    return int(Decimal(str(coins or 0)) * TOMAN_PER_COIN)


def toman_to_coins(toman: int | Decimal | float | str, rounding_policy: str = RoundingPolicy.FLOOR) -> int:
    value = Decimal(str(toman or 0)) / Decimal(TOMAN_PER_COIN)
    rounding = {RoundingPolicy.FLOOR: ROUND_FLOOR, RoundingPolicy.CEIL: ROUND_CEILING, RoundingPolicy.HALF_UP: ROUND_HALF_UP}.get(rounding_policy)
    if rounding is None:
        raise ValueError('unsupported_rounding_policy')
    return int(value.to_integral_value(rounding=rounding))


def format_persian_number(value: int | Decimal | float | str) -> str:
    try:
        n = Decimal(str(value))
        if n == n.to_integral_value():
            text = f"{int(n):,}"
        else:
            text = f"{n:,.2f}".rstrip('0').rstrip('.')
    except Exception:
        text = str(value)
    return text.translate(_PERSIAN_DIGITS)


def format_coins(coins: int | Decimal | float | str) -> str:
    return f"{format_persian_number(coins)} سکه"


def format_toman(toman: int | Decimal | float | str) -> str:
    return f"{format_persian_number(toman)} تومان"


def format_coin_toman_pair(coins: int | Decimal | float | str) -> str:
    return f"{format_coins(coins)} ({format_toman(coins_to_toman(coins))})"
