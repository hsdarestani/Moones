import pytest
from fastapi import HTTPException
from app.core.admin_security import AdminPrincipal, hash_token, verify_csrf
from app.models.admin_security import AdminSession


def test_missing_csrf_blocks_state_changes():
    p = AdminPrincipal(None, AdminSession(csrf_token_hash=hash_token("known")))
    with pytest.raises(HTTPException):
        verify_csrf(p, None)


def test_valid_csrf_permits_state_changes():
    p = AdminPrincipal(None, AdminSession(csrf_token_hash=hash_token("known")))
    verify_csrf(p, "known")


def test_all_admin_csrf_imports_use_context():
    from pathlib import Path

    root = Path("app/templates/admin")

    for template in root.rglob("*.html"):
        source = template.read_text(encoding="utf-8")

        if "admin/csrf.html" in source and "import" in source:
            for line in source.splitlines():
                if "admin/csrf.html" in line and "import" in line:
                    assert "with context" in line, (
                        f"CSRF import without context: {template}"
                    )

    csrf_source = (
        root / "csrf.html"
    ).read_text(encoding="utf-8")

    assert "request is defined" in csrf_source
