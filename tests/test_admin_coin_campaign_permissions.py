from app.core.admin_security import has_permission


def test_finance_and_owner_can_execute_viewers_cannot():
    assert has_permission("owner", "coin_gifts.manage")
    assert has_permission("finance", "coin_gifts.manage")
    assert not has_permission("viewer", "coin_gifts.manage")
    assert not has_permission("support", "coin_gifts.manage")
    assert not has_permission("operator", "coin_gifts.manage")
