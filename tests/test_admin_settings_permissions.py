from app.core.admin_security import has_permission
from app.services.settings_service import SETTING_REGISTRY


def test_billing_changes_require_finance_or_owner_permission():
    perm = SETTING_REGISTRY["billing.usd_to_toman"].required_permission
    assert perm == "settings.billing"
    assert has_permission("finance", perm)
    assert has_permission("owner", perm)
    assert not has_permission("operator", perm)


def test_safety_changes_require_elevated_permission():
    perm = SETTING_REGISTRY["image_generation.adult_enabled"].required_permission
    assert perm == "settings.safety"
    assert has_permission("operator", perm)
    assert not has_permission("viewer", perm)
