from app.core.admin_security import AdminAuditService


def test_secrets_absent_from_audit_metadata():
    scrubbed = AdminAuditService.scrub({"password": "secret", "nested": {"api_key": "key"}, "safe": "ok"})
    assert scrubbed["password"] == "[redacted]"
    assert scrubbed["nested"]["api_key"] == "[redacted]"
    assert scrubbed["safe"] == "ok"
