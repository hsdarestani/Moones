def test_wallet_adjustment_requires_permission_reason_confirmation_and_idempotency():
    src = open('app/api/admin.py').read()
    assert 'require_permission("wallet.adjust")' in src
    assert 'confirmation != "CONFIRM"' in src
    assert 'not reason' in src
    assert 'idempotency_key' in src
    assert 'wallet_service.credit' in src
    assert 'AdminAuditService.record' in src
