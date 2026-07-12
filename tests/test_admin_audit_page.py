from pathlib import Path
from app.core.admin_security import AdminAuditService


def test_audit_template_contains_filters_and_safe_diff():
    html = Path("app/templates/admin/audit.html").read_text()
    for name in ["admin_filter", "action", "target", "status", "date_from", "date_to"]:
        assert name in html
    assert "before_json" in html and "after_json" in html


def test_sensitive_values_do_not_enter_audit_metadata():
    scrubbed = AdminAuditService.scrub({"telegram_bot_token":"123", "provider_api_key":"abc", "database_url":"sqlite://", "safe":"ok"})
    assert scrubbed["telegram_bot_token"] == "[redacted]"
    assert scrubbed["provider_api_key"] == "[redacted]"
    assert scrubbed["database_url"] == "[redacted]"
    assert scrubbed["safe"] == "ok"
