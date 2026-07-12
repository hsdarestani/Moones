from app.core.admin_security import has_permission


def test_viewer_cannot_mutate_data():
    assert not has_permission("viewer", "wallets.adjust")


def test_support_cannot_adjust_wallets():
    assert not has_permission("support", "wallets.adjust")


def test_finance_can_access_payments_and_wallet_tools():
    assert has_permission("finance", "payments.read")
    assert has_permission("finance", "wallets.adjust")


def test_operator_can_retry_media_but_not_approve_payments():
    assert has_permission("operator", "generated_media.manage")
    assert not has_permission("operator", "payments.mutate")


def test_owner_has_full_access():
    assert has_permission("owner", "anything")
