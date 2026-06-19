from app.core.config import get_settings

ADMIN_CREDIT_ERROR = "مقدار واردشده بیش از حد مجازه. لطفاً عدد کوچک‌تری وارد کن."


def parse_admin_credit_amount(raw) -> tuple[int | None, str | None]:
    try:
        amount = int(raw or 0)
    except (TypeError, ValueError):
        return None, ADMIN_CREDIT_ERROR
    if amount <= 0 or amount > get_settings().admin_max_credit_amount:
        return None, ADMIN_CREDIT_ERROR
    return amount, None
