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

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.admin_security import SESSION_COOKIE, hash_password, hash_token
from app.db.base import Base
from app.models.admin_security import AdminAuditEvent, AdminSession, AdminUser
from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction


@pytest.fixture()
def admin_wallet_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine, tables=[
        AdminUser.__table__,
        AdminSession.__table__,
        AdminAuditEvent.__table__,
        User.__table__,
        Wallet.__table__,
        WalletTransaction.__table__,
    ])

    import app.db.session as db_session
    import app.main as main_module

    monkeypatch.setattr(db_session, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)

    token = "admin-session-token"
    csrf = hash_token("wallet-csrf")
    with TestingSessionLocal() as db:
        admin = AdminUser(
            username="finance-admin",
            password_hash=hash_password("very-secure-password"),
            role="finance",
            is_active=True,
        )
        user = User(telegram_id=99001, display_name="Wallet Test")
        db.add_all([admin, user])
        db.flush()
        db.add(AdminSession(
            admin_user_id=admin.id,
            token_hash=hash_token(token),
            csrf_token_hash=csrf,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
        ))
        db.add(Wallet(user_id=user.id, balance_coins=100, total_added_coins=100, total_spent_coins=0))
        db.commit()
        user_id = user.id

    client = TestClient(main_module.app)
    client.cookies.set(SESSION_COOKIE, token)
    try:
        yield client, TestingSessionLocal, user_id, csrf
    finally:
        Base.metadata.drop_all(bind=engine, tables=[
            WalletTransaction.__table__,
            Wallet.__table__,
            User.__table__,
            AdminAuditEvent.__table__,
            AdminSession.__table__,
            AdminUser.__table__,
        ])
        engine.dispose()


def _wallet_balance(session_factory, user_id):
    with session_factory() as db:
        return db.query(Wallet).filter(Wallet.user_id == user_id).one().balance_coins


def _wallet_payload(csrf, action="increase", amount="25", reason="manual adjustment"):
    return {
        "csrf_token": csrf,
        "action": action,
        "amount": amount,
        "reason": reason,
        "confirm": "CONFIRM",
        "idempotency_key": f"test-{action}-{amount}-{reason}",
    }


def test_plain_form_increase_succeeds(admin_wallet_client):
    client, session_factory, user_id, csrf = admin_wallet_client
    response = client.post(f"/admin/users/{user_id}/wallet/adjust", data=_wallet_payload(csrf, amount="۳۵"), follow_redirects=False)
    assert response.status_code == 303
    assert _wallet_balance(session_factory, user_id) == 135


def test_plain_form_decrease_succeeds(admin_wallet_client):
    client, session_factory, user_id, csrf = admin_wallet_client
    response = client.post(f"/admin/users/{user_id}/wallet/adjust", data=_wallet_payload(csrf, action="decrease", amount=" 1,5 "), follow_redirects=False)
    assert response.status_code == 303
    assert _wallet_balance(session_factory, user_id) == 85


def test_ajax_fetch_style_form_request_succeeds(admin_wallet_client):
    client, session_factory, user_id, csrf = admin_wallet_client
    payload = _wallet_payload(csrf, amount="20", reason="ajax")
    response = client.post(
        f"/admin/users/{user_id}/wallet/adjust",
        data=payload,
        headers={"Accept": "application/json", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["change"] == 20
    assert _wallet_balance(session_factory, user_id) == 120


def test_json_request_with_x_csrf_token_succeeds(admin_wallet_client):
    client, session_factory, user_id, csrf = admin_wallet_client
    payload = _wallet_payload("not-used-for-json", amount="40", reason="json")
    response = client.post(
        f"/admin/users/{user_id}/wallet/adjust",
        json=payload,
        headers={"Accept": "application/json", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["change"] == 40
    assert _wallet_balance(session_factory, user_id) == 140


@pytest.mark.parametrize("token", [None, "invalid"])
def test_missing_or_invalid_token_returns_403(admin_wallet_client, token):
    client, session_factory, user_id, csrf = admin_wallet_client
    payload = _wallet_payload(token or "", amount="10", reason="csrf-fail")
    headers = {"Accept": "application/json"}
    if token:
        headers["X-CSRF-Token"] = token
    response = client.post(f"/admin/users/{user_id}/wallet/adjust", data=payload, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid CSRF token"
    assert _wallet_balance(session_factory, user_id) == 100


def test_request_body_remains_readable_after_middleware_csrf_validation(admin_wallet_client):
    client, session_factory, user_id, csrf = admin_wallet_client
    response = client.post(
        f"/admin/users/{user_id}/wallet/adjust",
        data=_wallet_payload(csrf, action="decrease", amount="٢٥", reason="body-readable"),
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["change"] == -25
    assert _wallet_balance(session_factory, user_id) == 75


def test_wallet_template_avoids_action_dom_clobbering():
    src = open('app/templates/admin/user_wallet.html').read()
    assert 'name="action"' not in src
    assert 'name="operation"' in src
    assert 'form.getAttribute("action")' in src
    assert 'fetch(form.action' not in src


def test_get_adjust_endpoint_does_not_mutate_balance(admin_wallet_client):
    client, session_factory, user_id, csrf = admin_wallet_client
    response = client.get(f"/admin/users/{user_id}/wallet/adjust")
    assert response.status_code in {404, 405}
    assert _wallet_balance(session_factory, user_id) == 100


def test_missing_confirm_returns_400(admin_wallet_client):
    client, session_factory, user_id, csrf = admin_wallet_client
    payload = _wallet_payload(csrf, amount="10")
    payload["confirm"] = ""
    response = client.post(f"/admin/users/{user_id}/wallet/adjust", data=payload, headers={"Accept": "application/json"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Reason, non-zero amount and CONFIRM are required"
    assert _wallet_balance(session_factory, user_id) == 100


def test_repeated_idempotency_key_does_not_double_credit(admin_wallet_client):
    client, session_factory, user_id, csrf = admin_wallet_client
    payload = _wallet_payload(csrf, amount="10", reason="same-key")
    assert client.post(f"/admin/users/{user_id}/wallet/adjust", data=payload, follow_redirects=False).status_code == 303
    assert client.post(f"/admin/users/{user_id}/wallet/adjust", data=payload, follow_redirects=False).status_code == 303
    assert _wallet_balance(session_factory, user_id) == 110
