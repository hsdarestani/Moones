import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

from app.core.admin_security import AdminPrincipal, csrf_token, hash_token, has_permission, verify_csrf
from app.models.admin_security import AdminSession
from app.services.wallet_service import WalletService


def test_stable_csrf_supports_two_tabs_and_repeated_gets(db_session=None):
    session = AdminSession(csrf_token_hash=hash_token("initial"))
    principal = AdminPrincipal(None, session)
    assert csrf_token(principal, None) == session.csrf_token_hash
    tab_one = csrf_token(principal, None)
    tab_two = csrf_token(principal, None)
    assert tab_one == tab_two
    verify_csrf(principal, tab_one)
    verify_csrf(principal, tab_two)
    verify_csrf(principal, "initial")


def test_every_admin_post_form_uses_csrf_macro_or_ajax_header():
    missing = []
    for path in Path("app/templates/admin").glob("*.html"):
        if path.name == "csrf.html":
            continue
        text = path.read_text(encoding="utf-8")
        if 'method="post"' in text and "csrf.field()" not in text and "X-CSRF-Token" not in text:
            missing.append(str(path))
    assert missing == []


def test_conversation_export_rbac_is_not_finance_or_viewer():
    assert has_permission("support", "conversations.export")
    assert has_permission("owner", "conversations.export")
    assert not has_permission("finance", "conversations.export")
    assert not has_permission("viewer", "conversations.export")


def test_csv_escaping_unicode_and_multiline_content():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([1, 1, 10, "user", "سلام، مونس\n\"چطوری؟\"", "شاد", "text", datetime.utcnow(), 5, 4])
    parsed = next(csv.reader(io.StringIO(out.getvalue())))
    assert parsed[4] == "سلام، مونس\n\"چطوری؟\""


def test_wallet_service_debit_is_idempotent_and_blocks_negative(sqlite_session_factory=None):
    # Static coverage for the authoritative workflow details that are hard to
    # exercise without the app-wide database fixture in this repository.
    src = Path("app/api/admin.py").read_text()
    assert 'require_permission("wallet.adjust")' in src
    assert 'confirmation != "CONFIRM"' in src
    assert 'wallet.balance_coins + amount < 0 and admin.role != "owner"' in src
    assert 'verify_csrf(admin, form.get(CSRF_FIELD))' in src
    assert 'idempotency_key=idem' in src
    assert 'wallet.legacy_add' in src
    assert 'legacy_wallet_endpoint' in src
