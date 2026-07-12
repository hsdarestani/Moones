from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

import importlib.util

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.admin_security import AdminAuditEvent, AdminSession, AdminUser

SESSION_COOKIE = "moones_admin_session"
CSRF_FIELD = "csrf_token"
SESSION_DAYS = 1
MIN_PASSWORD_LENGTH = 12
if importlib.util.find_spec("argon2"):
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError
    _ph = PasswordHasher()
else:
    PasswordHasher = None
    VerifyMismatchError = VerificationError = Exception
    _ph = None
_basic = HTTPBasic(auto_error=False)

ROLE_PERMISSIONS = {
    "owner": {"*"},
    "finance": {"dashboard.read", "financial_metrics.read", "users.read", "payments.read", "payments.mutate", "wallets.read", "wallets.adjust", "wallet.adjust", "coin_gifts.manage", "addons.manage", "reports.read", "settings.billing", "settings.read_safe", "audit.read_limited"},
    "support": {"dashboard.read", "operations.read", "users.read", "conversations.read", "media.read", "memories.manage", "relationship.manage", "support.ops", "settings.read_safe", "audit.read_limited"},
    "operator": {"dashboard.read", "operations.read", "media.read", "generated_media.manage", "health.read", "settings.nonfinancial", "settings.operations", "settings.safety", "settings.read_safe", "proactive.manage", "audit.read_limited"},
    "viewer": {"dashboard.read", "users.read", "conversations.read", "media.read", "reports.read", "health.read", "settings.read_safe"},
}

ROUTE_PERMISSION_MAP = {
    "GET /admin": "dashboard.read", "GET /admin/users": "users.read", "GET /admin/users/export.csv": "reports.read", "GET /admin/users/{user_id}": "users.read", "GET /admin/users/{user_id}/*": "users.read",
    "GET /admin/live": "conversations.read", "GET /admin/api/live/messages": "conversations.read",
    "GET /admin/receipts": "payments.read", "POST /admin/payments/*": "payments.mutate",
    "POST /admin/users/{user_id}/wallet/adjust": "wallet.adjust", "POST /admin/users/{user_id}/subscription/*": "payments.mutate", "POST /admin/users/{user_id}/usage/reset": "support.ops",
    "POST /admin/users/{user_id}/reset-memory": "memories.manage", "POST /admin/users/{user_id}/reset-state": "relationship.manage",
    "GET /admin/addons": "payments.read", "POST /admin/addons/*": "addons.manage", "POST /admin/users/{user_id}/addons/*": "addons.manage",
    "GET /admin/coin-gifts": "coin_gifts.manage", "GET /admin/coin-campaigns*": "coin_gifts.manage", "POST /admin/coin-campaigns*": "coin_gifts.manage", "GET /admin/media": "media.read", "GET /admin/generated-media": "media.read",
    "POST /admin/image-generation/jobs/{job_id}/retry": "generated_media.manage", "POST /admin/users/{user_id}/visual-profile/reset": "generated_media.manage",
    "GET /admin/settings": "settings.read_safe", "POST /admin/settings": "settings.nonfinancial", "GET /admin/audit": "audit.read_limited", "GET /admin/health": "health.read", "GET /admin/operations": "operations.read", "GET /admin/exports/*": "reports.read", "GET /admin/admin-users": "admin_users.manage",
    "POST /admin/admin-users*": "admin_users.manage",
}

@dataclass
class AdminPrincipal:
    user: AdminUser | None
    session: AdminSession | None
    via_basic_fallback: bool = False
    @property
    def username(self): return self.user.username if self.user else "emergency-basic"
    @property
    def role(self): return self.user.role if self.user else "owner"


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()

def hash_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if _ph is not None:
        return _ph.hash(password)
    salt = secrets.token_hex(16)
    rounds = 600_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds).hex()
    return f"pbkdf2_sha256${rounds}${salt}${digest}"

def verify_password(stored_hash: str, password: str) -> tuple[bool, bool]:
    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt, digest = stored_hash.split("$", 3)
            candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
            return hmac.compare_digest(candidate, digest), False
        except ValueError:
            return False, False
    if _ph is None:
        return False, False
    try:
        ok = _ph.verify(stored_hash, password)
        return bool(ok), bool(ok and _ph.check_needs_rehash(stored_hash))
    except (VerifyMismatchError, VerificationError):
        return False, False

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def new_token() -> str:
    return secrets.token_urlsafe(48)

def has_permission(role: str, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or permission in perms

def current_admin(request: Request, db: Session) -> AdminPrincipal | None:
    token = request.cookies.get(SESSION_COOKIE)
    now = datetime.utcnow()
    if token:
        sess = db.execute(select(AdminSession).where(AdminSession.token_hash == hash_token(token))).scalar_one_or_none()
        if sess and not sess.revoked_at and sess.expires_at > now and sess.admin_user and sess.admin_user.is_active:
            sess.last_seen_at = now
            return AdminPrincipal(sess.admin_user, sess)
    return None

def require_admin(request: Request, db: Session = Depends(get_db), credentials: HTTPBasicCredentials | None = Depends(_basic)) -> AdminPrincipal:
    principal = current_admin(request, db)
    if principal:
        request.state.admin = principal
        if principal.session and not getattr(request.state, "csrf_token", None):
            request.state.csrf_token = csrf_token(principal, db)
        return principal
    settings = get_settings()
    if getattr(settings, "admin_basic_fallback_enabled", False) and credentials:
        if hmac.compare_digest(credentials.username, settings.admin_user) and hmac.compare_digest(credentials.password, settings.admin_password):
            principal = AdminPrincipal(None, None, True)
            request.state.admin = principal
            return principal
    wants_html = "text/html" in request.headers.get("accept", "")
    if wants_html:
        raise HTTPException(status_code=307, headers={"Location": "/admin/login"})
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin authentication required")

def require_permission(permission: str):
    def dep(principal: AdminPrincipal = Depends(require_admin)) -> AdminPrincipal:
        if not has_permission(principal.role, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return principal
    return dep

def csrf_token(principal: AdminPrincipal, db: Session) -> str:
    if not principal.session: return "basic-fallback"
    token = new_token()
    principal.session.csrf_token_hash = hash_token(token)
    db.flush()
    return token

def verify_csrf(principal: AdminPrincipal, token: str | None):
    if principal.via_basic_fallback: return
    expected = principal.session.csrf_token_hash if principal.session else None
    if not token or not expected or not hmac.compare_digest(hash_token(token), expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")

class AdminAuditService:
    SECRET_KEYS = {"password", "token", "api_key", "secret", "database_url", "db_url", "receipt_file", "raw_media", "conversation"}
    @staticmethod
    def scrub(value):
        if isinstance(value, dict):
            return {k: ("[redacted]" if any(s in k.lower() for s in AdminAuditService.SECRET_KEYS) else AdminAuditService.scrub(v)) for k, v in value.items()}
        if isinstance(value, list): return [AdminAuditService.scrub(v) for v in value[:50]]
        return value
    @staticmethod
    def record(db: Session, *, admin: AdminPrincipal | None, action: str, status: str, target_type: str | None = None, target_id: str | int | None = None, reason: str | None = None, before=None, after=None, metadata=None, request: Request | None = None):
        db.add(AdminAuditEvent(admin_user_id=admin.user.id if admin and admin.user else None, action=action, status=status, target_type=target_type, target_id=str(target_id) if target_id is not None else None, reason=reason, before_json=AdminAuditService.scrub(before), after_json=AdminAuditService.scrub(after), metadata_json=AdminAuditService.scrub(metadata), request_id=request.headers.get("x-request-id") if request else None))
