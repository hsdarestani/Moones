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
