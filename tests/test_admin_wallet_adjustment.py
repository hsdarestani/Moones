from app.api.admin import parse_admin_wallet_adjustment_payload


def test_wallet_adjustment_requires_permission_reason_confirmation_and_idempotency():
    src = open('app/api/admin.py').read()
    assert 'require_permission("wallet.adjust")' in src
    assert 'confirmation != "CONFIRM"' in src
    assert 'not reason' in src
    assert 'idempotency_key' in src
    assert 'wallet_service.credit' in src
    assert 'AdminAuditService.record' in src


def test_decrease_with_normal_english_digits():
    amount, reason, confirmation = parse_admin_wallet_adjustment_payload({"action": "decrease", "amount": "25", "reason": " manual debit ", "confirm": " CONFIRM "})
    assert (amount, reason, confirmation) == (-25, "manual debit", "CONFIRM")


def test_decrease_with_persian_digits():
    amount, reason, confirmation = parse_admin_wallet_adjustment_payload({"operation": "debit", "amount_coins": "۱۲۳", "reason": "کاهش", "confirmation": "CONFIRM"})
    assert amount == -123
    assert reason == "کاهش"
    assert confirmation == "CONFIRM"


def test_decrease_with_formatted_input():
    amount, _, _ = parse_admin_wallet_adjustment_payload({"type": "remove", "coins": " 1,500 ", "reason": "fee", "confirm_text": "CONFIRM"})
    assert amount == -1500


def test_increase_flow():
    amount, reason, confirmation = parse_admin_wallet_adjustment_payload({"action": "credit", "delta": "+٢,٥٠٠", "reason": "bonus", "confirm": "CONFIRM"})
    assert (amount, reason, confirmation) == (2500, "bonus", "CONFIRM")


def test_invalid_missing_confirm():
    amount, reason, confirmation = parse_admin_wallet_adjustment_payload({"action": "add", "amount_delta": "10", "reason": "bonus"})
    assert amount == 10
    assert reason == "bonus"
    assert confirmation != "CONFIRM"


def test_invalid_zero_amount():
    amount, reason, confirmation = parse_admin_wallet_adjustment_payload({"action": "decrease", "amount": "۰", "reason": "noop", "confirm": "CONFIRM"})
    assert amount == 0
    assert reason == "noop"
    assert confirmation == "CONFIRM"
