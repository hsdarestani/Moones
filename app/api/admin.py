from datetime import date, datetime, timedelta
import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import String, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.admin_security import AdminAuditService, AdminPrincipal, CSRF_FIELD, SESSION_COOKIE, csrf_token, hash_password, hash_token, new_token, normalize_username, require_admin, require_permission, verify_csrf, verify_password
from app.models.admin_security import AdminUser, AdminSession
from app.db.session import get_db
from app.engine.persona_voice_engine import generate_voice_profile
from app.engine.relationship_engine import ensure_relationship
from app.llm.client import LLMClient
from app.llm.response_processor import post_process_response
from app.memory.memory_manager import memory_summary
from app.services.onboarding_service import OnboardingService
from app.models.memory import MemoryItem
from app.models.message import Message
from app.models.relationship import Relationship, RelationshipStage
from app.models.user import User
from app.models.subscription import DailyUsage, Subscription
from app.models.wallet import Wallet, WalletTransaction
from app.models.admin_coin_campaign import AdminCoinCampaign, AdminCoinCampaignRecipient
from app.models.payment import PaymentReceipt
from app.services.subscription_service import SubscriptionService
from app.services.wallet_service import WalletService
from app.services.credit_validation import ADMIN_CREDIT_ERROR, parse_admin_credit_amount
from app.models.analytics import AnalyticsEvent
from app.models.proactive import ProactiveMessage
from app.models.support import SupportMessage
from app.models.style_audit import BotStyleAudit
from app.models.settings import AppSetting
from app.services.partner_style import build_partner_style_dna, active_style_lessons
from app.services.memory_digest import run_daily_memory_digest
from app.services.settings_service import SettingsService
from app.models.partner_life import PartnerLifeEvent, PartnerDailyRoutine
from app.models.human_delivery import HumanDeliveryJob
from app.models.media import MediaMessage
from app.services.partner_life_service import PartnerLifeService, get_or_create_today_event
from app.services.conversation_time_service import ConversationTimeService
from app.services.partner_routine_service import PartnerRoutineService
from app.services.style_audit import run_persian_audit
from app.models.addon import AddonProduct, UserAddon
from app.services.addon_service import AddonService, INTIMACY_MAX_UNLOCK
from app.models.usage import AiUsageEvent
from app.models.billing import UsageCharge
from app.services.plan_config import get_plan_configs
from app.services.usage_cost_service import estimate_llm_cost, record_ai_usage_event
from app.services.admin_metrics_service import AdminMetricsService
from app.services.admin_user_360_service import AdminFinancialLedgerService
from app.core.admin_security import has_permission
import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
wallet_service = WalletService()
subscription_service = SubscriptionService()
addon_service = AddonService()
ledger_service = AdminFinancialLedgerService()
templates = Jinja2Templates(directory="app/templates")




def _max_datetime(*values):
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _message_payload(message: Message, user: User | None = None) -> dict:
    return {
        "id": message.id,
        "user_id": message.user_id,
        "telegram_id": getattr(user, "telegram_id", None),
        "display_name": getattr(user, "display_name", None) or (f"User #{message.user_id}" if message.user_id else "—"),
        "role": message.role,
        "content": message.content or "",
        "emotion": message.emotion,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _with_last_activity(rows):
    enriched = []
    for row in rows:
        user = row[0]
        latest_message_at = row[-1]
        enriched.append((*row[:-1], _max_datetime(getattr(user, "last_seen_at", None), latest_message_at)))
    return enriched




@router.get("/login", response_class=HTMLResponse)
def admin_login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/login.html", {"error": request.query_params.get("error")})


@router.post("/login")
async def admin_login(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    form = await request.form()
    username = normalize_username(str(form.get("username") or ""))
    password = str(form.get("password") or "")
    user = db.execute(select(AdminUser).where(AdminUser.username == username)).scalar_one_or_none()
    principal = AdminPrincipal(user, None) if user else None
    if not user or not user.is_active:
        AdminAuditService.record(db, admin=principal, action="admin.login", status="failed", target_type="admin_user", target_id=username, reason="invalid_or_inactive", metadata={"username": username}, request=request)
        db.commit()
        return RedirectResponse("/admin/login?error=1", status_code=303)
    ok, rehash = verify_password(user.password_hash, password)
    if not ok:
        AdminAuditService.record(db, admin=principal, action="admin.login", status="failed", target_type="admin_user", target_id=user.id, reason="invalid_password", metadata={"username": username}, request=request)
        db.commit()
        return RedirectResponse("/admin/login?error=1", status_code=303)
    if rehash:
        user.password_hash = hash_password(password)
    now = datetime.utcnow()
    token = new_token()
    csrf = new_token()
    session = AdminSession(admin_user_id=user.id, token_hash=hash_token(token), csrf_token_hash=hash_token(csrf), created_at=now, expires_at=now + timedelta(days=1), last_seen_at=now, user_agent_summary=(request.headers.get("user-agent") or "")[:255])
    db.add(session)
    user.last_login_at = now
    AdminAuditService.record(db, admin=principal, action="admin.login", status="succeeded", target_type="admin_user", target_id=user.id, metadata={"username": username}, request=request)
    db.commit()
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=(get_settings().environment == "production"), max_age=86400)
    return response


@router.post("/logout")
def admin_logout(request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_admin)) -> RedirectResponse:
    if admin.session:
        admin.session.revoked_at = datetime.utcnow()
    AdminAuditService.record(db, admin=admin, action="admin.logout", status="succeeded", request=request)
    db.commit()
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response



@router.get("/admin-users", response_class=HTMLResponse)
def admin_users_page(request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("admin_users.manage"))) -> HTMLResponse:
    admins = db.execute(select(AdminUser).order_by(AdminUser.username)).scalars().all()
    return templates.TemplateResponse(request, "admin/admin_users.html", {"admins": admins, "roles": ["owner", "finance", "support", "operator", "viewer"]})


@router.post("/admin-users")
async def create_admin_user(request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("admin_users.manage"))) -> RedirectResponse:
    form = await request.form()
    username = normalize_username(str(form.get("username") or ""))
    role = str(form.get("role") or "viewer")
    reason = str(form.get("reason") or "")
    if not reason:
        raise HTTPException(status_code=400, detail="Reason is required")
    user = AdminUser(username=username, display_name=str(form.get("display_name") or "")[:255], role=role, password_hash=hash_password(str(form.get("password") or "")), is_active=True, must_change_password=True)
    db.add(user)
    db.flush()
    AdminAuditService.record(db, admin=admin, action="admin_user.create", status="succeeded", target_type="admin_user", target_id=user.id, reason=reason, after={"username": username, "role": role}, request=request)
    db.commit()
    return RedirectResponse("/admin/admin-users", status_code=303)


def _active_owner_count(db: Session) -> int:
    return db.execute(select(func.count(AdminUser.id)).where(AdminUser.role == "owner", AdminUser.is_active.is_(True))).scalar_one()


@router.post("/admin-users/{admin_id}/toggle")
async def toggle_admin_user(admin_id: int, request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("admin_users.manage"))) -> RedirectResponse:
    form = await request.form(); reason = str(form.get("reason") or "")
    target = db.get(AdminUser, admin_id)
    if not target: raise HTTPException(status_code=404)
    if target.is_active and target.role == "owner" and _active_owner_count(db) <= 1:
        AdminAuditService.record(db, admin=admin, action="admin_user.deactivate", status="failed", target_type="admin_user", target_id=admin_id, reason="last_owner", request=request); db.commit(); raise HTTPException(status_code=400, detail="Cannot deactivate last active owner")
    before = {"is_active": target.is_active, "role": target.role}
    target.is_active = not target.is_active
    AdminAuditService.record(db, admin=admin, action="admin_user.toggle_active", status="succeeded", target_type="admin_user", target_id=admin_id, reason=reason, before=before, after={"is_active": target.is_active, "role": target.role}, request=request)
    db.commit(); return RedirectResponse("/admin/admin-users", status_code=303)


@router.post("/admin-users/{admin_id}/revoke-sessions")
async def revoke_admin_sessions(admin_id: int, request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("admin_users.manage"))) -> RedirectResponse:
    form = await request.form(); reason = str(form.get("reason") or "")
    target = db.get(AdminUser, admin_id)
    if not target: raise HTTPException(status_code=404)
    if admin.user and target.id == admin.user.id and _active_owner_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Cannot revoke your only-owner session")
    now = datetime.utcnow()
    for sess in target.sessions:
        if not sess.revoked_at: sess.revoked_at = now
    AdminAuditService.record(db, admin=admin, action="admin_user.revoke_sessions", status="succeeded", target_type="admin_user", target_id=admin_id, reason=reason, request=request)
    db.commit(); return RedirectResponse("/admin/admin-users", status_code=303)


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, range: str = "last_30_days", timezone: str = "Asia/Tehran", start: str | None = None, end: str | None = None, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("dashboard.read"))) -> HTMLResponse:
    service = AdminMetricsService(db)
    metrics_range = service.build_range(range, timezone, start, end)
    overview = service.dashboard(metrics_range, admin.role)
    return templates.TemplateResponse(request, "admin/dashboard.html", {"overview": overview, "range": metrics_range.key, "timezone": timezone, "start": start or "", "end": end or ""})


@router.get("/operations", response_class=HTMLResponse)
def operations_center(request: Request, range: str = "last_30_days", timezone: str = "Asia/Tehran", db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("operations.read"))) -> HTMLResponse:
    service = AdminMetricsService(db); metrics_range = service.build_range(range, timezone)
    ops = service.operations_summary(metrics_range)
    return templates.TemplateResponse(request, "admin/operations.html", {"operations": ops, "alerts": service.alerts(ops), "range": metrics_range.key, "timezone": timezone})


def _csv_stream(rows, header):
    def gen():
        buf = io.StringIO(); writer = csv.writer(buf); writer.writerow(header); yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        for row in rows:
            writer.writerow(row); yield buf.getvalue(); buf.seek(0); buf.truncate(0)
    return StreamingResponse(gen(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=admin-export.csv"})

@router.get("/exports/{kind}.csv")
def admin_export(kind: str, range: str = "last_30_days", timezone: str = "Asia/Tehran", db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("reports.read"))):
    service = AdminMetricsService(db); r = service.build_range(range, timezone)
    if kind == "financial-daily":
        if admin.role not in {"owner", "finance"}: raise HTTPException(status_code=403)
        f = service.financial_summary(r); return _csv_stream([(k, v) for k, v in f.items()], ["metric", "value"])
    if kind == "usage-by-feature-model":
        return _csv_stream(([x.get("feature"), x.get("provider"), x.get("model"), x.get("status"), x.get("requests"), x.get("charged_coins"), x.get("refunded_coins"), x.get("provider_cost")] for x in service.usage_breakdown(r)), ["feature", "provider", "model", "status", "requests", "charged_coins", "refunded_coins", "provider_cost"])
    if kind == "refund-report":
        rows = db.execute(select(UsageCharge.id, UsageCharge.user_id, UsageCharge.refunded_coins, UsageCharge.refunded_at).where(UsageCharge.refunded_at >= r.start_utc, UsageCharge.refunded_at < r.end_utc).limit(10000)).all(); return _csv_stream(rows, ["charge_id", "user_id", "refunded_coins", "refunded_at"])
    if kind == "operational-failure-report":
        rows = [(a["severity"], a["title"], a["count"]) for a in service.alerts(service.operations_summary(r))]; return _csv_stream(rows, ["severity", "title", "count"])
    raise HTTPException(status_code=404)



@router.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    plan: str | None = None,
    stage: str | None = None,
    q: str | None = None,
    telegram_id: int | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    active_addon: str | None = None,
    proactive: str | None = None,
    min_balance: int | None = None,
    max_balance: int | None = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = "last_activity",
    direction: str = "desc",
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    page = max(page, 1); page_size = min(max(page_size, 1), 100)
    latest = func.max(Message.created_at).label("latest_message_at")
    total = func.count(Message.id).label("total_messages")
    query = select(User, Relationship, Wallet, Subscription, total, latest).outerjoin(Relationship, Relationship.user_id == User.id).outerjoin(Wallet, Wallet.user_id == User.id).outerjoin(Subscription, (Subscription.user_id == User.id) & (Subscription.status == "active")).outerjoin(Message, Message.user_id == User.id).group_by(User.id, Relationship.id, Wallet.id, Subscription.id)
    count_query = select(func.count(func.distinct(User.id))).outerjoin(Relationship, Relationship.user_id == User.id).outerjoin(Wallet, Wallet.user_id == User.id).outerjoin(Subscription, (Subscription.user_id == User.id) & (Subscription.status == "active"))
    conditions = []
    if q:
        needle=f"%{q}%"; conditions.append((User.display_name.ilike(needle)) | (User.telegram_id.cast(String).ilike(needle)))
    if telegram_id: conditions.append(User.telegram_id == telegram_id)
    if stage: conditions.append(Relationship.stage == stage)
    if plan: conditions.append(Subscription.plan == plan)
    if created_from: conditions.append(User.created_at >= datetime.fromisoformat(created_from))
    if created_to: conditions.append(User.created_at < datetime.fromisoformat(created_to) + timedelta(days=1))
    if min_balance is not None: conditions.append(Wallet.balance_coins >= min_balance)
    if max_balance is not None: conditions.append(Wallet.balance_coins <= max_balance)
    if active_addon:
        query = query.join(UserAddon, (UserAddon.user_id == User.id) & (UserAddon.status == "active") & (UserAddon.addon_key == active_addon))
        count_query = count_query.join(UserAddon, (UserAddon.user_id == User.id) & (UserAddon.status == "active") & (UserAddon.addon_key == active_addon))
    if proactive == "on": conditions.append(User.proactive_messages_enabled.is_(True))
    if proactive == "off": conditions.append(User.proactive_messages_enabled.is_(False))
    if conditions:
        query=query.where(and_(*conditions)); count_query=count_query.where(and_(*conditions))
    order_map={"created": User.created_at, "balance": Wallet.balance_coins, "telegram_id": User.telegram_id, "last_activity": func.coalesce(func.max(Message.created_at), User.last_seen_at)}
    order_col=order_map.get(sort, order_map["last_activity"])
    query=query.order_by(order_col.asc() if direction == "asc" else order_col.desc()).offset((page-1)*page_size).limit(page_size)
    users = _with_last_activity(db.execute(query).all())
    total_users = db.scalar(count_query) or 0
    return templates.TemplateResponse(request, "admin/users.html", {"users": users, "plan": plan or "", "stage": stage or "", "q": q or "", "stages": [s.value for s in RelationshipStage], "page": page, "page_size": page_size, "total_users": total_users, "sort": sort, "direction": direction, "params": dict(request.query_params)})

@router.get("/users/export.csv")
def users_export_csv(request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("reports.read"))) -> StreamingResponse:
    rows = db.execute(select(User.id, User.telegram_id, User.display_name, User.created_at, User.last_seen_at, Wallet.balance_coins).outerjoin(Wallet, Wallet.user_id == User.id).order_by(User.id.asc()).limit(10000)).all()
    return _csv_stream(rows, ["id", "telegram_id", "display_name", "created_at", "last_seen_at", "wallet_balance"])


@router.get("/live", response_class=HTMLResponse)
def live_messages_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/live_messages.html", {"range": ""})


@router.get("/api/live/messages")
def live_messages_api(
    limit: int = 80,
    after_id: int | None = None,
    user_id: int | None = None,
    telegram_id: int | None = None,
    role: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> JSONResponse:
    safe_limit = min(max(int(limit or 80), 1), 200)
    stmt = select(Message, User).outerjoin(User, Message.user_id == User.id)
    filters = []
    if after_id is not None:
        filters.append(Message.id > after_id)
    if user_id is not None:
        filters.append(Message.user_id == user_id)
    if telegram_id is not None:
        filters.append(User.telegram_id == telegram_id)
    if role:
        filters.append(Message.role == role)
    if q:
        needle = f"%{q.strip()}%"
        filters.append((Message.content.ilike(needle)) | (User.display_name.ilike(needle)) | (User.telegram_id.cast(String).ilike(needle)))
    if filters:
        stmt = stmt.where(and_(*filters))
    stmt = stmt.order_by(Message.id.asc() if after_id is not None else Message.id.desc()).limit(safe_limit)
    rows = db.execute(stmt).all()
    messages = [_message_payload(message, user) for message, user in rows]
    latest_id = max([m["id"] for m in messages], default=after_id or 0)
    return JSONResponse({"messages": messages, "latest_id": latest_id, "count": len(messages)})




@router.get("/api/media/messages")
def media_messages_api(
    limit: int = 50,
    media_ref: str | None = None,
    user_id: int | None = None,
    telegram_user_id: int | None = None,
    support_message_id: int | None = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> JSONResponse:
    safe_limit = min(max(int(limit or 50), 1), 200)
    stmt = select(MediaMessage, User).join(User, MediaMessage.user_id == User.id)
    filters = []
    if media_ref:
        filters.append(MediaMessage.media_ref.ilike(f"%{media_ref.strip()}%"))
    if user_id is not None:
        filters.append(MediaMessage.user_id == user_id)
    if telegram_user_id is not None:
        filters.append(User.telegram_id == telegram_user_id)
    if support_message_id is not None:
        filters.append(MediaMessage.support_message_id == support_message_id)
    if filters:
        stmt = stmt.where(and_(*filters))
    rows = db.execute(stmt.order_by(MediaMessage.created_at.desc()).limit(safe_limit)).all()
    return JSONResponse({"media_messages": [{
        "id": m.id, "media_ref": m.media_ref, "user_id": m.user_id, "telegram_user_id": u.telegram_id,
        "kind": m.kind, "file_size": m.file_size, "width": m.width, "height": m.height, "duration_seconds": float(m.duration_seconds) if m.duration_seconds is not None else None,
        "support_forward_status": m.support_forward_status, "support_message_id": m.support_message_id,
        "processing_status": m.processing_status, "vision_model": m.vision_model, "stt_model": m.stt_model, "error": m.error,
        "has_raw_preview": bool(m.stored_path and get_settings().store_raw_user_images),
        "created_at": m.created_at.isoformat() if m.created_at else None,
    } for m, u in rows]})

@router.get("/api/users/{user_id}/activity")
def user_activity_api(user_id: int, range_name: str = Query("7d", alias="range"), db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    if range_name not in {"7d", "14d", "30d"}:
        range_name = "7d"
    days = int(range_name[:-1])
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days - 1)
    end_date = today + timedelta(days=1)
    labels_dates = [start_date + timedelta(days=i) for i in range(days)]
    labels = [d.strftime("%m/%d") for d in labels_dates]
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.min.time())
    day_col = func.date(Message.created_at)
    rows = db.execute(select(day_col, Message.role, func.count(Message.id)).where(Message.user_id == user_id, Message.created_at >= start_dt, Message.created_at < end_dt).group_by(day_col, Message.role)).all()
    user_counts = {d.isoformat(): 0 for d in labels_dates}
    assistant_counts = {d.isoformat(): 0 for d in labels_dates}
    for day, msg_role, count in rows:
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        if msg_role == "user":
            user_counts[key] = int(count or 0)
        elif msg_role in {"assistant", "assistant_debug"}:
            assistant_counts[key] = assistant_counts.get(key, 0) + int(count or 0)
    user_series = [user_counts[d.isoformat()] for d in labels_dates]
    assistant_series = [assistant_counts[d.isoformat()] for d in labels_dates]
    voice = db.scalar(select(func.coalesce(func.sum(DailyUsage.daily_voice_sent), 0)).where(DailyUsage.user_id == user_id, DailyUsage.date >= start_date, DailyUsage.date < end_date)) or 0
    stickers = db.scalar(select(func.coalesce(func.sum(DailyUsage.daily_stickers_sent), 0)).where(DailyUsage.user_id == user_id, DailyUsage.date >= start_date, DailyUsage.date < end_date)) or 0
    proactive = db.scalar(select(func.count(ProactiveMessage.id)).where(ProactiveMessage.user_id == user_id, ProactiveMessage.sent_at >= start_dt, ProactiveMessage.sent_at < end_dt)) or 0
    text = sum(user_series) + sum(assistant_series)
    return JSONResponse({"labels": labels, "messages": {"user": user_series, "assistant": assistant_series, "total": [u+a for u,a in zip(user_series, assistant_series)]}, "delivery": {"text": int(text), "voice": int(voice), "sticker": int(stickers), "proactive": int(proactive)}, "meta": {"source": "database"}})


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, range: str = "30d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/analytics.html", {"range": range, "overview": _analytics_overview(db, range)})


@router.get("/api/analytics/overview")
def analytics_overview(range: str = "30d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    return JSONResponse(_analytics_overview(db, range))


@router.get("/api/analytics/revenue")
def analytics_revenue(range: str = "30d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    start, end, labels = _range(range)
    revenue = _series(db, PaymentReceipt.created_at, func.coalesce(func.sum(PaymentReceipt.amount_toman), 0), PaymentReceipt.status == "approved", start=start, end=end)
    by_plan = _revenue_by_plan(db, start, end)
    return JSONResponse({"labels": labels, "revenue": _align(labels, revenue), "by_plan": by_plan, "funnel": _payment_funnel(db, start, end)})


@router.get("/api/analytics/users")
def analytics_users(range: str = "30d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    start, end, labels = _range(range)
    new_users = _series(db, User.created_at, func.count(User.id), start=start, end=end)
    active = _series(db, Message.created_at, func.count(func.distinct(Message.user_id)), start=start, end=end)
    return JSONResponse({"labels": labels, "new_users": _align(labels, new_users), "active_users": _align(labels, active), "plan_distribution": _plan_distribution(db), "retention_estimate": _retention_summary(db, start, end)})


@router.get("/api/analytics/plans")
def analytics_plans(range: str = "30d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    start, end, labels = _range(range)
    return JSONResponse({"labels": labels, "distribution": _plan_distribution(db), "revenue_by_plan": _revenue_by_plan(db, start, end), "mrr": _mrr(db), "expiring": _expiring(db)})


@router.get("/api/analytics/behavior")
def analytics_behavior(range: str = "30d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    start, end, labels = _range(range)
    total = _series(db, Message.created_at, func.count(Message.id), start=start, end=end)
    voice = _usage_series(db, DailyUsage.daily_voice_sent, start.date(), end.date())
    stickers = _usage_series(db, DailyUsage.daily_stickers_sent, start.date(), end.date())
    return JSONResponse({"labels": labels, "messages": _align(labels, total), "voice": _align(labels, voice), "stickers": _align(labels, stickers), "delivery_mix": _delivery_mix(db, start, end), "tokens": _token_series(db, labels, start.date(), end.date())})


@router.get("/api/analytics/partners")
def analytics_partners(range: str = "30d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    return JSONResponse(_partner_analytics(db))


@router.get("/api/analytics/proactive")
def analytics_proactive(range: str = "30d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    start, end, labels = _range(range)
    created = _series(db, ProactiveMessage.created_at, func.count(ProactiveMessage.id), start=start, end=end)
    sent = _series(db, ProactiveMessage.sent_at, func.count(ProactiveMessage.id), ProactiveMessage.sent_at.is_not(None), start=start, end=end)
    skipped = _event_series(db, "proactive_skipped", start, end)
    replied = _event_series(db, "proactive_replied", start, end)
    intent_rows = db.execute(select(ProactiveMessage.intent, func.count(ProactiveMessage.id)).where(ProactiveMessage.sent_at >= start, ProactiveMessage.sent_at < end).group_by(ProactiveMessage.intent)).all()
    hour_rows = db.execute(select(func.extract('hour', ProactiveMessage.sent_at), func.count(ProactiveMessage.id)).where(ProactiveMessage.sent_at >= start, ProactiveMessage.sent_at < end).group_by(func.extract('hour', ProactiveMessage.sent_at))).all()
    sent_rows = db.scalars(select(ProactiveMessage).where(ProactiveMessage.sent_at >= start, ProactiveMessage.sent_at < end).limit(1000)).all()
    q_ratio = round((sum(1 for m in sent_rows if (m.text or '').strip().endswith(('؟','?'))) / len(sent_rows)) * 100, 2) if sent_rows else 0
    return JSONResponse({"labels": labels, "scheduled": _align(labels, created), "sent": _align(labels, sent), "skipped": _align(labels, skipped), "replied": _align(labels, replied), "sent_by_intent": {str(k or 'unknown'): int(v) for k,v in intent_rows}, "sent_by_hour": {str(k or 'unknown'): int(v) for k,v in hour_rows}, "question_ending_ratio": q_ratio})


@router.get("/api/analytics/support")
def analytics_support(range: str = "30d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    start, end, labels = _range(range)
    opened = _series(db, SupportMessage.created_at, func.count(SupportMessage.id), start=start, end=end)
    replied = _series(db, SupportMessage.replied_at, func.count(SupportMessage.id), SupportMessage.replied_at.is_not(None), start=start, end=end)
    return JSONResponse({"labels": labels, "opened": _align(labels, opened), "replied": _align(labels, replied), "open_count": db.scalar(select(func.count(SupportMessage.id)).where(SupportMessage.status == "open")) or 0})


@router.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail(
    user_id: int,
    request: Request,
    q: str | None = None,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    state = ensure_relationship(user.id, user.relationship_state)
    messages = []  # Overview intentionally avoids loading full conversation history.
    memories = db.scalars(select(MemoryItem).where(MemoryItem.user_id == user.id).order_by(MemoryItem.created_at.desc()).limit(25)).all()
    wallet = wallet_service.get_or_create_wallet(db, user)
    subscription = subscription_service.get_active_subscription(db, user) or subscription_service.ensure_free_subscription(db, user)
    usage = subscription_service.get_or_create_today_usage(db, user)
    receipts = db.scalars(select(PaymentReceipt).where(PaymentReceipt.user_id == user.id).order_by(PaymentReceipt.created_at.desc()).limit(20)).all()
    recent_proactive = db.scalars(select(ProactiveMessage).where(ProactiveMessage.user_id == user.id).order_by(ProactiveMessage.created_at.desc()).limit(5)).all()
    last_user_message = db.scalar(select(Message).where(Message.user_id == user.id, Message.role == "user").order_by(Message.created_at.desc()).limit(1))
    admin_time_context = ConversationTimeService().build_context(db, user)
    routine_service = PartnerRoutineService()
    admin_routine = routine_service.get_or_create_for_context(db, user, admin_time_context)
    admin_routine_slot = routine_service.current_slot(admin_routine, admin_time_context)
    today_life_event = get_or_create_today_event(db, user, local_date=admin_time_context.local_date)
    recent_life_events = db.scalars(select(PartnerLifeEvent).where(PartnerLifeEvent.user_id == user.id).order_by(PartnerLifeEvent.event_date.desc(), PartnerLifeEvent.created_at.desc()).limit(5)).all()
    today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    proactive_today_count = db.scalar(select(func.count(ProactiveMessage.id)).where(ProactiveMessage.user_id == user.id, ProactiveMessage.sent_at >= today_start)) or 0
    recent_human_jobs = db.scalars(select(HumanDeliveryJob).where(HumanDeliveryJob.user_id == user.id).order_by(HumanDeliveryJob.created_at.desc()).limit(10)).all()
    pending_human_jobs = db.scalars(select(HumanDeliveryJob).where(HumanDeliveryJob.user_id == user.id, HumanDeliveryJob.status == "pending").order_by(HumanDeliveryJob.scheduled_at.asc()).limit(10)).all()
    human_job_stats_today = db.execute(select(HumanDeliveryJob.job_type, HumanDeliveryJob.status, func.count(HumanDeliveryJob.id)).where(HumanDeliveryJob.user_id == user.id, HumanDeliveryJob.created_at >= today_start).group_by(HumanDeliveryJob.job_type, HumanDeliveryJob.status)).all()
    latest_message_at = db.scalar(select(func.max(Message.created_at)).where(Message.user_id == user.id))
    last_activity_at = _max_datetime(user.last_seen_at, latest_message_at)
    partner_profile = OnboardingService().partner_profile(user)
    generated_voice_profile = generate_voice_profile(partner_profile, state, memories)
    partner_style_dna = build_partner_style_dna(user, state, [m.content for m in memories[:8]])
    last_digest = db.scalar(select(AppSetting.value).where(AppSetting.key == f"memory.last_digest_at.{user.id}"))
    inspector = {
        "time_context": {"timezone": admin_time_context.timezone_name, "current_local_time": admin_time_context.local_now.isoformat(), "last_user_message_at": getattr(user, "last_user_message_at", None), "last_assistant_message_at": getattr(user, "last_assistant_message_at", None), "last_gap_bucket": getattr(user, "last_gap_bucket", None), "current_routine_slot": admin_routine_slot.get("slot_name"), "current_routine_city": admin_routine.city, "routine_json": admin_routine.schedule_json},
        "partner_profile": partner_profile,
        "generated_voice_profile": user.last_voice_profile or generated_voice_profile,
        "relationship_state": state,
        "emotion_state": _latest_emotion(db, user.id),
        "memory_summary": memory_summary(db, user.id),
        "last_prompt": user.last_prompt or "No prompt captured yet.",
        "last_user_message": _latest_user_message(db, user.id) or "—",
        "detected_intent": _situation_field(user.last_detected_situation, "intent"),
        "confidence": _situation_field(user.last_detected_situation, "confidence"),
        "matched_keywords": _situation_field(user.last_detected_situation, "matched_keywords"),
        "model": user.last_llm_model or "—",
        "raw_venice_response_text": getattr(user, "last_raw_llm_response", None) or "—",
        "extracted_text": user.last_llm_response or "—",
        "extraction_path": getattr(user, "last_llm_extraction_path", None) or "—",
        "retry_used": getattr(user, "last_llm_retry_used", False),
        "last_llm_response": user.last_llm_response or "No response captured yet.",
        "last_processed_response": user.last_processed_response or "No processed response captured yet.",
        "detected_situation": user.last_detected_situation or "—",
        "fallback_used": user.last_fallback_used,
        "fallback_reason": user.last_fallback_reason or "—",
        "simple_intent_bypass": user.last_simple_intent_bypass,
        "latency_breakdown": user.last_latency_breakdown or "{}",
        "llm_called": user.last_llm_called,
        "context_reset": user.last_context_reset,
        "safety_flag": user.last_safety_flag,
        "quality_gate_reason": user.last_quality_gate_reason or "—",
        "context_messages_used": user.last_context_messages_used or "[]",
        "final_response": user.last_processed_response or "—",
        "raw_response": user.last_llm_response or "—",
        "garbage_filter_triggered": user.last_garbage_filter_triggered,
        "repetition_filter_triggered": user.last_repetition_filter_triggered,
        "wallet": wallet,
        "subscription": subscription,
        "usage": usage,
        "receipts": receipts,
        "partner_style_dna": partner_style_dna,
        "human_presence": {"energy": getattr(user, "current_mood", None) or "calm", "recent_jobs": recent_human_jobs, "pending_jobs": pending_human_jobs, "stats_today": human_job_stats_today},
        "natural_style": {"current_tone": "casual/plain", "poetry_allowed": False, "romance_allowed": False, "last_style_correction": "see recent messages", "last_guard_violation": "see style audits", "emotional_loop_guard": "enabled", "recent_audit_issues": [r.issue_type for r in db.scalars(select(BotStyleAudit).where(BotStyleAudit.user_id == user.id).order_by(BotStyleAudit.created_at.desc()).limit(8)).all()]},
        "selected_memories": memories[:8],
        "last_memory_digest_at": last_digest or "—",
        "recent_proactive": recent_proactive,
        "recent_life_events": recent_life_events,
        "latest_life_event": today_life_event or (recent_life_events[0] if recent_life_events else None),
        "last_proactive": recent_proactive[0] if recent_proactive else None,
        "last_input_type": getattr(last_user_message, "input_type", "text") if last_user_message else "—",
        "last_voice_transcript": (last_user_message.content if last_user_message and getattr(last_user_message, "input_type", "text") in {"voice", "audio"} else None),
        "proactive_cooldown_status": "cooldown" if user.next_proactive_at and user.next_proactive_at > datetime.utcnow() else "available",
        "last_proactive_kind": (recent_proactive[0].intent if recent_proactive else None),
        "last_proactive_reply_followup": bool(((recent_proactive[0].extra_metadata or {}).get("reply_to_telegram_message_id")) if recent_proactive else False),
        "last_proactive_reply_to": ((recent_proactive[0].extra_metadata or {}).get("reply_to_telegram_message_id") if recent_proactive else None),
        "proactive_today_count": proactive_today_count,
        "proactive_daily_cap": SettingsService().get_int(db, "proactive.daily_max_per_user", 2),
        "proactive_send_window": f"{SettingsService().get_str(db, 'proactive.send_window_start', '10:30')}–{SettingsService().get_str(db, 'proactive.send_window_end', '23:30')}",
        "active_style_lessons": active_style_lessons(db, 10),
        "user_addons": db.scalars(select(UserAddon).where(UserAddon.user_id == user.id).order_by(UserAddon.created_at.desc())).all(),
        "latest_message_at": latest_message_at,
        "last_activity_at": last_activity_at,
    }
    return templates.TemplateResponse(
        request,
        "admin/user_detail.html",
        {"user": user, "state": state, "messages": messages, "memories": memories, "inspector": inspector, "stages": [stage.value for stage in RelationshipStage], "q": q or "", "start": start or "", "end": end or ""},
    )



@router.get("/users/{user_id}/conversation", response_class=HTMLResponse)
def user_conversation_tab(user_id: int, request: Request, role: str | None = None, q: str | None = None, start: str | None = None, end: str | None = None, page: int = 1, page_size: int = 50, sort: str = "newest", db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("conversations.read"))) -> HTMLResponse:
    user = db.get(User, user_id)
    if user is None: raise HTTPException(status_code=404, detail="User not found")
    page=max(page,1); page_size=min(max(page_size,1),200)
    filters=[Message.user_id == user_id]
    if role: filters.append(Message.role == role)
    if q: filters.append(Message.content.ilike(f"%{q}%"))
    if start: filters.append(Message.created_at >= datetime.fromisoformat(start))
    if end: filters.append(Message.created_at < datetime.fromisoformat(end) + timedelta(days=1))
    total=db.scalar(select(func.count(Message.id)).where(and_(*filters))) or 0
    order=Message.created_at.asc() if sort == "oldest" else Message.created_at.desc()
    messages=db.scalars(select(Message).where(and_(*filters)).order_by(order).offset((page-1)*page_size).limit(page_size)).all()
    can_read_text = admin.role not in {"finance"}
    return templates.TemplateResponse(request, "admin/user_conversation.html", {"user": user, "messages": messages, "can_read_text": can_read_text, "page": page, "page_size": page_size, "total": total, "role": role or "", "q": q or "", "start": start or "", "end": end or "", "sort": sort})

@router.get("/users/{user_id}/conversation/export.csv")
def user_conversation_export(user_id: int, role: str | None = None, q: str | None = None, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("reports.read"))) -> StreamingResponse:
    if admin.role == "finance": raise HTTPException(status_code=403, detail="Conversation export denied")
    filters=[Message.user_id == user_id]
    if role: filters.append(Message.role == role)
    if q: filters.append(Message.content.ilike(f"%{q}%"))
    rows=db.execute(select(Message.id, Message.role, Message.content, Message.created_at).where(and_(*filters)).order_by(Message.created_at.desc()).limit(10000)).all()
    return _csv_stream(rows, ["id", "role", "content", "created_at"])

@router.get("/users/{user_id}/wallet", response_class=HTMLResponse)
def user_wallet_tab(user_id: int, request: Request, page: int = 1, page_size: int = 50, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("wallets.read"))) -> HTMLResponse:
    user=db.get(User,user_id)
    if user is None: raise HTTPException(status_code=404, detail="User not found")
    wallet=wallet_service.get_or_create_wallet(db,user)
    reconciliation=ledger_service.reconciliation(db,user_id)
    rows=ledger_service.rows(db,user_id, limit=min(max(page_size,1),100), offset=(max(page,1)-1)*page_size)
    return templates.TemplateResponse(request,"admin/user_wallet.html",{"user":user,"wallet":wallet,"ledger_rows":rows,"reconciliation":reconciliation,"page":page,"page_size":page_size,"can_adjust": has_permission(admin.role, "wallet.adjust")})

@router.post("/users/{user_id}/wallet/adjust")
async def admin_wallet_adjust(user_id: int, request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("wallet.adjust"))) -> RedirectResponse:
    user=db.get(User,user_id)
    if user is None: raise HTTPException(status_code=404, detail="User not found")
    form=await request.form(); amount=int(form.get("amount") or 0); reason=str(form.get("reason") or "").strip(); confirmation=str(form.get("confirm") or "").strip(); idem=str(form.get("idempotency_key") or f"admin-adjust:{user_id}:{uuid.uuid4()}")
    wallet=wallet_service.get_or_create_wallet(db,user)
    if not reason or confirmation != "CONFIRM" or amount == 0: raise HTTPException(status_code=400, detail="Reason, non-zero amount and CONFIRM are required")
    if wallet.balance_coins + amount < 0 and admin.role != "owner": raise HTTPException(status_code=400, detail="Negative resulting balance requires owner recovery")
    before={"balance": wallet.balance_coins}; metadata={"admin_action": True, "admin": admin.username, "reason": reason}
    if amount > 0: wallet_service.credit(db,user,amount,reason="admin_wallet_adjustment",metadata=metadata,idempotency_key=idem)
    else: wallet_service.debit(db,user,abs(amount),reason="admin_wallet_adjustment",metadata=metadata)
    AdminAuditService.record(db, admin=admin, action="wallet.adjust", status="succeeded", target_type="user", target_id=user_id, reason=reason, before=before, after={"balance": wallet.balance_coins, "change": amount}, metadata={"idempotency_key": idem}, request=request)
    db.commit(); return RedirectResponse(f"/admin/users/{user_id}/wallet", status_code=303)

@router.get("/users/{user_id}/billing", response_class=HTMLResponse)
def user_billing_tab(user_id:int, request:Request, feature:str|None=None, status:str|None=None, model:str|None=None, start:str|None=None, end:str|None=None, page:int=1, page_size:int=50, db:Session=Depends(get_db), admin:AdminPrincipal=Depends(require_permission("payments.read"))) -> HTMLResponse:
    user=db.get(User,user_id)
    if user is None: raise HTTPException(status_code=404, detail="User not found")
    filters=[UsageCharge.user_id==user_id]
    if feature: filters.append(UsageCharge.feature==feature)
    if status: filters.append(UsageCharge.status==status)
    if model: filters.append(UsageCharge.model.ilike(f"%{model}%"))
    if start: filters.append(UsageCharge.created_at >= datetime.fromisoformat(start))
    if end: filters.append(UsageCharge.created_at < datetime.fromisoformat(end)+timedelta(days=1))
    charges=db.scalars(select(UsageCharge).where(and_(*filters)).order_by(UsageCharge.created_at.desc()).offset((max(page,1)-1)*page_size).limit(min(max(page_size,1),100))).all()
    return templates.TemplateResponse(request,"admin/user_billing.html",{"user":user,"charges":charges,"show_costs": admin.role not in {"support","viewer"}})

@router.get("/users/{user_id}/memory", response_class=HTMLResponse)
def user_memory_tab(user_id:int, request:Request, type:str|None=None, page:int=1, page_size:int=50, db:Session=Depends(get_db), admin:AdminPrincipal=Depends(require_permission("memories.manage"))) -> HTMLResponse:
    user=db.get(User,user_id)
    if user is None: raise HTTPException(status_code=404, detail="User not found")
    filters=[MemoryItem.user_id==user_id]
    if type: filters.append(MemoryItem.type==type)
    memories=db.scalars(select(MemoryItem).where(and_(*filters)).order_by(MemoryItem.created_at.desc()).offset((max(page,1)-1)*page_size).limit(min(max(page_size,1),100))).all()
    return templates.TemplateResponse(request,"admin/user_memory.html",{"user":user,"memories":memories,"advanced": admin.role in {"owner","operator"}})

@router.get("/users/{user_id}/{tab}", response_class=HTMLResponse)
def user_simple_tab(user_id:int, tab:str, request:Request, db:Session=Depends(get_db), admin:AdminPrincipal=Depends(require_admin)) -> HTMLResponse:
    if tab not in {"media","relationship","proactive","support","actions"}: raise HTTPException(status_code=404)
    user=db.get(User,user_id)
    if user is None: raise HTTPException(status_code=404, detail="User not found")
    ctx={"user":user,"tab":tab,"advanced": admin.role in {"owner","operator"}}
    if tab == "media": ctx["media_rows"] = db.scalars(select(MediaMessage).where(MediaMessage.user_id==user_id).order_by(MediaMessage.created_at.desc()).limit(100)).all()
    if tab == "support": ctx["support_rows"] = db.scalars(select(SupportMessage).where(SupportMessage.user_id==user_id).order_by(SupportMessage.created_at.desc()).limit(100)).all()
    if tab == "relationship": ctx["state"] = ensure_relationship(user.id, user.relationship_state); ctx["events"] = db.scalars(select(PartnerLifeEvent).where(PartnerLifeEvent.user_id==user_id).order_by(PartnerLifeEvent.created_at.desc()).limit(20)).all()
    if tab == "proactive": ctx["proactive_rows"] = db.scalars(select(ProactiveMessage).where(ProactiveMessage.user_id==user_id).order_by(ProactiveMessage.created_at.desc()).limit(50)).all()
    return templates.TemplateResponse(request,"admin/user_simple_tab.html",ctx)
@router.post("/users/{user_id}/run-memory-digest")
def admin_run_memory_digest(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    run_daily_memory_digest(db, datetime.utcnow().date(), user_id=user_id)
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)

@router.post("/style-audit/run-now")
def admin_run_style_audit(db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    run_persian_audit(db, limit=300)
    db.commit()
    return RedirectResponse("/admin/style-audit", status_code=303)

@router.get("/style-audit", response_class=HTMLResponse)
def admin_style_audit(request: Request, range: str = "7d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    days = int(range[:-1]) if range.endswith("d") and range[:-1].isdigit() else 7
    start = datetime.utcnow() - timedelta(days=days)
    counts = db.execute(select(BotStyleAudit.issue_type, func.count(BotStyleAudit.id)).where(BotStyleAudit.created_at >= start).group_by(BotStyleAudit.issue_type)).all()
    source_count = (db.scalar(select(func.count(Message.id)).where(Message.role.in_(["assistant", "assistant_debug"]))) or 0) + (db.scalar(select(func.count(ProactiveMessage.id)).where(ProactiveMessage.text.is_not(None))) or 0)
    examples = db.scalars(select(BotStyleAudit).where(BotStyleAudit.created_at >= start).order_by(BotStyleAudit.created_at.desc()).limit(100)).all()
    lessons = active_style_lessons(db, 30)
    return templates.TemplateResponse(request, "admin/style_audit.html", {"range": range, "counts": counts, "examples": examples, "lessons": lessons, "source_count": source_count})

@router.post("/users/{user_id}/run-life-event")
async def admin_run_life_event(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await PartnerLifeService().create_for_user(db, user)
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)

@router.post("/users/{user_id}/wallet/add")
async def admin_add_coins(user_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    form = await request.form()
    amount, error = parse_admin_credit_amount(form.get("amount", 0))
    if error:
        return RedirectResponse(f"/admin/users/{user_id}?error=credit", status_code=303)
    wallet_service.credit(db, user, amount, reason="admin_add", metadata={"admin_action": True})
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/wallet/subtract")
async def admin_subtract_coins(user_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    form = await request.form()
    amount, error = parse_admin_credit_amount(form.get("amount", 0))
    if error:
        return RedirectResponse(f"/admin/users/{user_id}?error=credit", status_code=303)
    wallet_service.debit(db, user, amount, reason="admin_subtract", metadata={"admin_action": True})
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/subscription/{plan}")
def admin_activate_subscription(user_id: int, plan: str, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    subscription_service.activate_plan(db, user, plan)
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/subscription/cancel")
def admin_cancel_subscription(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    subscription_service.cancel(db, user)
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/usage/reset")
def admin_reset_daily_usage(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    subscription_service.reset_today_usage(db, user)
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/reset-state")
def reset_state(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    rel = db.scalar(select(Relationship).where(Relationship.user_id == user_id))
    if rel:
        rel.intimacy = 0.05
        rel.attachment = 0.05
        rel.trust = 0.05
        rel.dependency = 0.0
        rel.attraction = 0.03
        rel.volatility = 0.2
        rel.stage = RelationshipStage.STRANGER.value
        rel.daily_streak = 0
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/reset-memory")
def reset_memory(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    for item in db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id)).all():
        db.delete(item)
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/force-stage")
async def force_stage(user_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    stage = str(form.get("stage", RelationshipStage.STRANGER.value))
    if stage not in {item.value for item in RelationshipStage}:
        raise HTTPException(status_code=400, detail="Invalid stage")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    rel = ensure_relationship(user.id, user.relationship_state)
    if rel.id is None:
        db.add(rel)
    rel.stage = stage
    db.add(MemoryItem(user_id=user.id, type="relationship_milestone", content=f"Admin forced relationship stage to {stage}.", importance_score=0.8))
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/rerun-last-prompt")
async def rerun_last_prompt(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None or not user.last_prompt:
        raise HTTPException(status_code=404, detail="Last prompt not found")
    messages = _parse_captured_prompt(user.last_prompt)
    raw = await LLMClient().complete(messages)
    processed = post_process_response(raw)
    response = processed[0] if isinstance(processed, tuple) else processed
    user.last_llm_response = raw
    user.last_processed_response = response
    db.add(Message(user_id=user.id, role="assistant_debug", content=response))
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


def _analytics(db: Session) -> dict[str, float | int]:
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    dau = db.scalar(select(func.count(User.id)).where(User.last_seen_at >= day_ago)) or 0
    total_users = db.scalar(select(func.count(User.id))) or 0
    total_messages = db.scalar(select(func.count(Message.id))) or 0
    avg_messages = round(total_messages / total_users, 2) if total_users else 0
    today_usage = db.scalar(select(func.coalesce(func.sum(DailyUsage.input_tokens + DailyUsage.output_tokens + DailyUsage.voice_tokens), 0)).where(DailyUsage.date == date.today())) or 0
    voice_usage = db.scalar(select(func.coalesce(func.sum(DailyUsage.daily_voice_sent), 0)).where(DailyUsage.date == date.today())) or 0
    stickers_sent = db.scalar(select(func.coalesce(func.sum(DailyUsage.daily_stickers_sent), 0)).where(DailyUsage.date == date.today())) or 0
    pending_receipts = db.scalar(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "pending")) or 0
    sessions = db.execute(select(Message.user_id, func.min(Message.created_at), func.max(Message.created_at)).group_by(Message.user_id)).all()
    avg_session = 0
    if sessions:
        avg_session = round(sum((row[2] - row[1]).total_seconds() / 60 for row in sessions) / len(sessions), 2)
    return {
        "dau": dau,
        "avg_messages_per_user": avg_messages,
        "retention_d1": _retention(db, 1),
        "retention_d3": _retention(db, 3),
        "retention_d7": _retention(db, 7),
        "avg_session_length": avg_session,
        "total_users": total_users,
        "tokens_used_today": int(today_usage),
        "cost_estimate": round((int(today_usage) / 1000) * 0.001, 4),
        "voice_usage": int(voice_usage),
        "stickers_sent": int(stickers_sent),
        "pending_receipts": pending_receipts,
    }


def _retention(db: Session, days: int) -> float:
    cutoff = datetime.utcnow() - timedelta(days=days)
    cohort = db.scalars(select(User).where(User.created_at <= cutoff)).all()
    if not cohort:
        return 0
    retained = sum(1 for user in cohort if user.last_seen_at >= user.created_at + timedelta(days=days))
    return round((retained / len(cohort)) * 100, 2)


def _latest_emotion(db: Session, user_id: int) -> str:
    message = db.scalar(select(Message).where(Message.user_id == user_id, Message.emotion.is_not(None)).order_by(Message.created_at.desc()).limit(1))
    return message.emotion if message and message.emotion else "neutral"


def _parse_captured_prompt(captured: str) -> list[dict[str, str]]:
    messages = []
    for part in captured.split("\n\n"):
        role, _, content = part.partition(": ")
        if role in {"system", "user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return messages or [{"role": "user", "content": captured}]

from app.models.settings import AppSetting
from app.models.payment import PaymentReceipt
from app.models.sticker import StickerPack, StickerItem
from app.services.settings_service import SettingsService, DEFAULT_SETTINGS
settings_service = SettingsService()



BULK_GIFT_CONFIRM_PHRASE = "هدیه به همه کاربران"
COIN_CAMPAIGN_CONFIRM_PREFIX = "EXECUTE"
COIN_CAMPAIGN_BATCH_SIZE = 100


def _campaign_key(title: str | None = None) -> str:
    """Return an immutable UUID campaign key; title is ignored for safety."""
    return str(uuid.uuid4())



def _parse_coin_campaign_amount(raw) -> int:
    amount, error = parse_admin_credit_amount(raw)
    if error or amount is None:
        return 0
    return int(amount)

def _coin_campaign_limits(db: Session) -> dict:
    return {
        "max_per_user": settings_service.get_int(db, "admin.coin_campaign.max_coins_per_user", 100000),
        "large_total": settings_service.get_int(db, "admin.coin_campaign.large_total_coins", 1000000),
    }


def _campaign_confirmation_phrase(campaign: AdminCoinCampaign) -> str:
    return f"{COIN_CAMPAIGN_CONFIRM_PREFIX} {campaign.campaign_key}"


def _campaign_audience_users(db: Session, audience_type: str = "all_users") -> list[User]:
    if audience_type != "all_users":
        raise HTTPException(status_code=400, detail="Unsupported audience")
    return list(db.scalars(select(User).order_by(User.id)).all())


def _safe_error(exc: Exception) -> tuple[str, str]:
    return type(exc).__name__[:64], str(exc).split("\n", 1)[0][:500]


def _recalculate_campaign_counts(db: Session, campaign: AdminCoinCampaign) -> None:
    rows = db.execute(select(AdminCoinCampaignRecipient.status, func.count(AdminCoinCampaignRecipient.id)).where(AdminCoinCampaignRecipient.campaign_id == campaign.id).group_by(AdminCoinCampaignRecipient.status)).all()
    counts = {status: count for status, count in rows}
    campaign.target_count = sum(counts.values())
    campaign.credited_count = counts.get("credited", 0)
    campaign.skipped_count = counts.get("already_credited", 0) + counts.get("excluded", 0)
    campaign.failed_count = counts.get("failed", 0)
    campaign.total_credited_coins = campaign.credited_count * campaign.amount_coins


def _preview_payload(db: Session, amount: int, title: str, note: str, campaign_key: str | None = None) -> dict:
    users = _campaign_audience_users(db)
    total = amount * len(users)
    toman = total  # one coin ~= one Toman in admin estimate until pricing service exposes a dedicated conversion.
    warnings = []
    limits = _coin_campaign_limits(db)
    if amount > limits["max_per_user"]: warnings.append("amount_exceeds_max_per_user")
    if total >= limits["large_total"]: warnings.append("large_campaign_requires_password")
    return {"amount": amount, "title": title, "note": note, "audience": "all_users", "campaign_key": campaign_key or _campaign_key(), "target_count": len(users), "total_coins": total, "approx_toman": toman, "samples": users[:10], "warnings": warnings}


def _execute_campaign_recipients(campaign_id: int, request: Request | None = None, admin: AdminPrincipal | None = None, limit: int = COIN_CAMPAIGN_BATCH_SIZE, session_factory=None) -> None:
    processed = failed = 0
    while True:
        campaign = db_campaign = None
        # Process one recipient per transaction so a failure cannot roll back successful recipients.
        if session_factory is None:
            from app.db.session import SessionLocal as session_factory
        with session_factory() as txdb:
            campaign = txdb.get(AdminCoinCampaign, campaign_id)
            if not campaign or campaign.status == "cancelled":
                break
            rec = txdb.scalar(select(AdminCoinCampaignRecipient).where(AdminCoinCampaignRecipient.campaign_id == campaign_id, AdminCoinCampaignRecipient.status == "pending").order_by(AdminCoinCampaignRecipient.id).limit(1))
            if not rec or processed >= limit:
                _recalculate_campaign_counts(txdb, campaign)
                remaining = txdb.scalar(select(func.count(AdminCoinCampaignRecipient.id)).where(AdminCoinCampaignRecipient.campaign_id == campaign_id, AdminCoinCampaignRecipient.status == "pending"))
                if remaining == 0 and campaign.status != "cancelled":
                    campaign.status = "partially_failed" if campaign.failed_count else "completed"
                    campaign.completed_at = datetime.utcnow()
                    AdminAuditService.record(txdb, admin=admin, action="coin_campaign.complete", status="succeeded", target_type="admin_coin_campaign", target_id=campaign.id, metadata={"failed": campaign.failed_count, "credited": campaign.credited_count}, request=request)
                txdb.commit()
                break
            rec.attempt_count += 1
            user = txdb.get(User, rec.user_id)
            try:
                idem = f"admin_bulk_gift:{campaign.campaign_key}:{rec.user_id}"
                existing = txdb.scalar(select(WalletTransaction).where(WalletTransaction.idempotency_key == idem))
                if existing:
                    rec.status = "already_credited"
                    rec.wallet_transaction_id = existing.id
                    rec.credited_at = existing.created_at
                else:
                    wallet_service.credit(txdb, user, campaign.amount_coins, reason="admin_bulk_gift", metadata={"admin_id": campaign.created_by_admin_id, "campaign_id": campaign.id, "campaign_key": campaign.campaign_key, "campaign_title": campaign.title, "admin_note": campaign.admin_note, "audience": campaign.audience_type}, idempotency_key=idem)
                    txdb.flush()
                    wt = txdb.scalar(select(WalletTransaction).where(WalletTransaction.idempotency_key == idem))
                    rec.status = "credited"
                    rec.wallet_transaction_id = wt.id if wt else None
                    rec.credited_at = datetime.utcnow()
                rec.error_code = rec.error_message = None
                _recalculate_campaign_counts(txdb, campaign)
                txdb.commit(); processed += 1
            except Exception as exc:
                txdb.rollback()
                with session_factory() as errdb:
                    c = errdb.get(AdminCoinCampaign, campaign_id); r = errdb.get(AdminCoinCampaignRecipient, rec.id)
                    if r:
                        code, msg = _safe_error(exc); r.status = "failed"; r.error_code = code; r.error_message = msg; r.attempt_count += 1
                    if c:
                        _recalculate_campaign_counts(errdb, c)
                    errdb.commit()
                failed += 1; processed += 1
    if failed:
        logger.warning("ADMIN_COIN_CAMPAIGN_BATCH_FAILED campaign_id=%s failed=%s", campaign_id, failed)


@router.get("/coin-gifts")
def admin_coin_gifts_redirect(_: AdminPrincipal = Depends(require_permission("coin_gifts.manage"))) -> RedirectResponse:
    return RedirectResponse("/admin/coin-campaigns", status_code=307)

@router.get("/coin-campaigns", response_class=HTMLResponse)
def admin_coin_campaigns(request: Request, db: Session = Depends(get_db), _: AdminPrincipal = Depends(require_permission("coin_gifts.manage"))) -> HTMLResponse:
    campaigns = db.scalars(select(AdminCoinCampaign).order_by(AdminCoinCampaign.created_at.desc()).limit(100)).all()
    return templates.TemplateResponse(request, "admin/coin_campaigns.html", {"campaigns": campaigns})

@router.get("/coin-campaigns/new", response_class=HTMLResponse)
def admin_coin_campaign_new(request: Request, db: Session = Depends(get_db), _: AdminPrincipal = Depends(require_permission("coin_gifts.manage"))) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/coin_campaign_new.html", {"preview": None, "error": "", "limits": _coin_campaign_limits(db)})

@router.post("/coin-campaigns/preview", response_class=HTMLResponse)
async def admin_coin_campaign_preview(request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("coin_gifts.manage"))) -> HTMLResponse:
    form = await request.form(); verify_csrf(admin, form.get(CSRF_FIELD))
    amount = _parse_coin_campaign_amount(form.get("amount") or form.get("amount_coins"))
    title = str(form.get("title") or "").strip(); note = str(form.get("note") or form.get("admin_note") or "").strip()
    limits = _coin_campaign_limits(db)
    if not title or not note or amount <= 0 or amount > limits["max_per_user"]:
        return templates.TemplateResponse(request, "admin/coin_campaign_new.html", {"preview": None, "error": "Title, note and a positive amount within configured limits are required.", "limits": limits})
    preview = _preview_payload(db, amount, title, note)
    AdminAuditService.record(db, admin=admin, action="coin_campaign.preview", status="succeeded", target_type="admin_coin_campaign", target_id=preview["campaign_key"], metadata={"target_count": preview["target_count"], "amount": amount}, request=request); db.commit()
    return templates.TemplateResponse(request, "admin/coin_campaign_new.html", {"preview": preview, "error": "", "limits": limits})

@router.post("/coin-campaigns")
async def admin_coin_campaign_create(request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("coin_gifts.manage"))) -> RedirectResponse:
    form = await request.form(); verify_csrf(admin, form.get(CSRF_FIELD))
    amount = _parse_coin_campaign_amount(form.get("amount") or form.get("amount_coins"))
    title = str(form.get("title") or "").strip(); note = str(form.get("note") or form.get("admin_note") or "").strip()
    limits = _coin_campaign_limits(db)
    if not title or not note or amount <= 0 or amount > limits["max_per_user"]: raise HTTPException(status_code=400, detail=ADMIN_CREDIT_ERROR)
    campaign = AdminCoinCampaign(campaign_key=str(form.get("campaign_key") or _campaign_key()), title=title, admin_note=note, amount_coins=amount, audience_type="all_users", audience_json={}, status="previewed", created_by_admin_id=admin.user.id if admin.user else None, previewed_at=datetime.utcnow())
    db.add(campaign); db.flush()
    for user in _campaign_audience_users(db): db.add(AdminCoinCampaignRecipient(campaign_id=campaign.id, user_id=user.id))
    db.flush(); _recalculate_campaign_counts(db, campaign)
    AdminAuditService.record(db, admin=admin, action="coin_campaign.create", status="succeeded", target_type="admin_coin_campaign", target_id=campaign.id, after={"campaign_key": campaign.campaign_key, "target_count": campaign.target_count, "amount": amount}, request=request)
    db.commit(); return RedirectResponse(f"/admin/coin-campaigns/{campaign.id}", status_code=303)

@router.get("/coin-campaigns/{campaign_id}", response_class=HTMLResponse)
def admin_coin_campaign_detail(campaign_id: int, request: Request, status_filter: str | None = Query(None, alias="status"), db: Session = Depends(get_db), _: AdminPrincipal = Depends(require_permission("coin_gifts.manage"))) -> HTMLResponse:
    campaign = db.get(AdminCoinCampaign, campaign_id)
    if not campaign: raise HTTPException(status_code=404)
    _recalculate_campaign_counts(db, campaign); db.flush()
    q = select(AdminCoinCampaignRecipient, User).join(User, User.id == AdminCoinCampaignRecipient.user_id).where(AdminCoinCampaignRecipient.campaign_id == campaign.id).order_by(AdminCoinCampaignRecipient.id).limit(200)
    if status_filter: q = q.where(AdminCoinCampaignRecipient.status == status_filter)
    recipients = db.execute(q).all()
    audits = []
    try:
        from app.models.admin_security import AdminAuditEvent
        audits = db.scalars(select(AdminAuditEvent).where(AdminAuditEvent.target_type == "admin_coin_campaign", AdminAuditEvent.target_id == str(campaign.id)).order_by(AdminAuditEvent.created_at.desc()).limit(20)).all()
    except Exception: audits = []
    return templates.TemplateResponse(request, "admin/coin_campaign_detail.html", {"campaign": campaign, "recipients": recipients, "audits": audits, "confirmation_phrase": _campaign_confirmation_phrase(campaign), "large_total": _coin_campaign_limits(db)["large_total"]})

async def _start_or_resume_campaign(campaign_id: int, request: Request, db: Session, admin: AdminPrincipal, action: str) -> RedirectResponse:
    form = await request.form(); verify_csrf(admin, form.get(CSRF_FIELD))
    campaign = db.get(AdminCoinCampaign, campaign_id)
    if not campaign: raise HTTPException(status_code=404)
    if not campaign.admin_note: raise HTTPException(status_code=400, detail="Mandatory note missing")
    if str(form.get("confirmation") or "") != _campaign_confirmation_phrase(campaign): raise HTTPException(status_code=400, detail="Invalid confirmation")
    if campaign.amount_coins * campaign.target_count >= _coin_campaign_limits(db)["large_total"]:
        ok, _ = verify_password(admin.user.password_hash if admin.user else "", str(form.get("admin_password") or ""))
        if not ok: raise HTTPException(status_code=403, detail="Password re-verification required")
    if campaign.status == "cancelled": raise HTTPException(status_code=400, detail="Campaign is cancelled")
    campaign.status = "running"; campaign.started_at = campaign.started_at or datetime.utcnow()
    if action == "resume":
        for failed_rec in db.scalars(select(AdminCoinCampaignRecipient).where(AdminCoinCampaignRecipient.campaign_id == campaign.id, AdminCoinCampaignRecipient.status == "failed")):
            failed_rec.status = "pending"
    AdminAuditService.record(db, admin=admin, action=f"coin_campaign.{action}", status="succeeded", target_type="admin_coin_campaign", target_id=campaign.id, request=request)
    db.commit()
    _execute_campaign_recipients(campaign.id, request=request, admin=admin)
    return RedirectResponse(f"/admin/coin-campaigns/{campaign.id}", status_code=303)

@router.post("/coin-campaigns/{campaign_id}/execute")
async def admin_coin_campaign_execute(campaign_id: int, request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("coin_gifts.manage"))) -> RedirectResponse:
    return await _start_or_resume_campaign(campaign_id, request, db, admin, "execute_start")

@router.post("/coin-campaigns/{campaign_id}/resume")
async def admin_coin_campaign_resume(campaign_id: int, request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("coin_gifts.manage"))) -> RedirectResponse:
    return await _start_or_resume_campaign(campaign_id, request, db, admin, "resume")

@router.post("/coin-campaigns/{campaign_id}/cancel")
async def admin_coin_campaign_cancel(campaign_id: int, request: Request, db: Session = Depends(get_db), admin: AdminPrincipal = Depends(require_permission("coin_gifts.manage"))) -> RedirectResponse:
    form = await request.form(); verify_csrf(admin, form.get(CSRF_FIELD))
    campaign = db.get(AdminCoinCampaign, campaign_id)
    if not campaign: raise HTTPException(status_code=404)
    campaign.status = "cancelled"; campaign.cancelled_at = datetime.utcnow()
    AdminAuditService.record(db, admin=admin, action="coin_campaign.cancel", status="succeeded", target_type="admin_coin_campaign", target_id=campaign.id, reason=str(form.get("reason") or ""), request=request)
    db.commit(); return RedirectResponse(f"/admin/coin-campaigns/{campaign.id}", status_code=303)

@router.get("/coin-campaigns/{campaign_id}/export.csv")
def admin_coin_campaign_export(campaign_id: int, db: Session = Depends(get_db), _: AdminPrincipal = Depends(require_permission("coin_gifts.manage"))) -> StreamingResponse:
    campaign = db.get(AdminCoinCampaign, campaign_id)
    if not campaign: raise HTTPException(status_code=404)
    out = io.StringIO(); writer = csv.writer(out); writer.writerow(["campaign_id","campaign_key","user_id","status","wallet_transaction_id","error_code","error_message","attempt_count","credited_at"])
    for r in db.scalars(select(AdminCoinCampaignRecipient).where(AdminCoinCampaignRecipient.campaign_id == campaign.id).order_by(AdminCoinCampaignRecipient.id)):
        writer.writerow([campaign.id, campaign.campaign_key, r.user_id, r.status, r.wallet_transaction_id or "", r.error_code or "", r.error_message or "", r.attempt_count, r.credited_at.isoformat() if r.credited_at else ""])
    out.seek(0); return StreamingResponse(iter([out.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=coin-campaign-{campaign.id}.csv"})

@router.get("/addons", response_class=HTMLResponse)
def admin_addons(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    addon_service.list_active_addons(db); db.commit()
    products = db.scalars(select(AddonProduct).order_by(AddonProduct.sort_order, AddonProduct.id)).all()
    purchases = db.scalars(select(UserAddon).order_by(UserAddon.created_at.desc()).limit(100)).all()
    return templates.TemplateResponse(request, "admin/addons.html", {"products": products, "purchases": purchases})

@router.post("/addons/{addon_key}/price")
async def admin_addon_price(addon_key: str, request: Request, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form(); product = db.scalar(select(AddonProduct).where(AddonProduct.key == addon_key))
    if product:
        old = product.price_toman; new = int(form.get("price_toman") or old); product.price_toman = new; SettingsService().set_value(db, "addon_intimacy_max_price_toman", str(new), "integer")
        logger.info("ADDON_PRICE_UPDATED admin_id=%s addon_key=%s old_price=%s new_price=%s", admin_id, addon_key, old, new)
    db.commit(); return RedirectResponse("/admin/addons", status_code=303)


@router.post("/addons/{addon_key}/metadata")
async def admin_addon_metadata(addon_key: str, request: Request, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)) -> RedirectResponse:
    product = db.scalar(select(AddonProduct).where(AddonProduct.key == addon_key))
    if product:
        form = await request.form()
        meta = product.metadata_json if isinstance(product.metadata_json, dict) else {}
        def lines(name: str) -> list[str]:
            return [x.strip() for x in str(form.get(name) or "").splitlines() if x.strip()]
        def intval(name: str, default: int) -> int:
            try:
                return int(form.get(name) or default)
            except Exception:
                return default
        meta.update({
            "upsell_enabled": bool(form.get("upsell_enabled")),
            "requires_adult": bool(form.get("requires_adult")),
            "trigger_keywords": lines("trigger_keywords"),
            "negative_keywords": lines("negative_keywords"),
            "upsell_title": str(form.get("upsell_title") or ""),
            "upsell_text": str(form.get("upsell_text") or ""),
            "cta_text": str(form.get("cta_text") or ""),
            "cooldown_hours": intval("cooldown_hours", 24),
            "max_suggestions_per_7d": intval("max_suggestions_per_7d", 2),
            "management_deeplink": str(form.get("management_deeplink") or ""),
        })
        product.metadata_json = meta
        logger.info("ADDON_METADATA_UPDATED admin_id=%s addon_key=%s", admin_id, addon_key)
    db.commit(); return RedirectResponse("/admin/addons", status_code=303)

@router.post("/addons/{addon_key}/toggle")
def admin_addon_toggle(addon_key: str, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    product = db.scalar(select(AddonProduct).where(AddonProduct.key == addon_key))
    if product: product.is_active = not product.is_active
    db.commit(); return RedirectResponse("/admin/addons", status_code=303)

@router.post("/users/{user_id}/addons/{addon_key}/grant")
def admin_grant_addon(user_id: int, addon_key: str, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)) -> RedirectResponse:
    addon_service.activate_addon_for_user(db, user_id=user_id, addon_key=addon_key, source="admin_grant")
    logger.info("ADDON_GRANTED admin_id=%s user_id=%s addon_key=%s", admin_id, user_id, addon_key)
    db.commit(); return RedirectResponse(f"/admin/users/{user_id}", status_code=303)

@router.post("/users/{user_id}/addons/{addon_key}/revoke")
def admin_revoke_addon(user_id: int, addon_key: str, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)) -> RedirectResponse:
    ua = db.scalar(select(UserAddon).where(UserAddon.user_id == user_id, UserAddon.addon_key == addon_key))
    if ua: ua.status = "revoked"
    user = db.get(User, user_id)
    if user and addon_key == INTIMACY_MAX_UNLOCK: user.intimacy_override_max=False; user.mature_intimacy_unlocked=False
    logger.info("ADDON_REVOKED admin_id=%s user_id=%s addon_key=%s", admin_id, user_id, addon_key)
    db.commit(); return RedirectResponse(f"/admin/users/{user_id}", status_code=303)

@router.get("/support", response_class=HTMLResponse)
def admin_support(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    tickets = db.scalars(select(SupportMessage).order_by(SupportMessage.created_at.desc()).limit(200)).all()
    return templates.TemplateResponse(request, "admin/support.html", {"tickets": tickets, "range": ""})

@router.get("/settings", response_class=HTMLResponse)
def admin_settings(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    settings_service.seed_defaults(db); db.commit()
    rows = db.scalars(select(AppSetting).order_by(AppSetting.key)).all()
    return templates.TemplateResponse(request, "admin/settings.html", {"settings": rows})

@router.post("/settings")
async def admin_settings_save(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    for key, (_, typ, _) in DEFAULT_SETTINGS.items():
        if key in form:
            settings_service.set_value(db, key, form.get(key, ""), typ)
    db.commit()
    return RedirectResponse("/admin/settings", status_code=303)

@router.get("/receipts", response_class=HTMLResponse)
def admin_receipts(request: Request, status_filter: str | None = None, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    q = select(PaymentReceipt).order_by(PaymentReceipt.created_at.desc())
    if status_filter:
        q = q.where(PaymentReceipt.status == status_filter)
    receipts = db.scalars(q.limit(200)).all()
    pending = db.scalar(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "pending")) or 0
    return templates.TemplateResponse(request, "admin/receipts.html", {"receipts": receipts, "pending": pending, "status_filter": status_filter or ""})

@router.post("/receipts/{receipt_id}/approve")
async def admin_approve_receipt(receipt_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    rec = db.get(PaymentReceipt, receipt_id)
    if not rec or rec.status != "pending":
        return RedirectResponse("/admin/receipts", status_code=303)
    form = await request.form(); paid_toman, error = parse_admin_credit_amount(form.get("paid_toman", form.get("coins", 0)))
    from app.services.coin_formatting_service import toman_to_coins, TOMAN_PER_COIN, RoundingPolicy
    coins = toman_to_coins(paid_toman, RoundingPolicy.FLOOR) if not error else None
    if error:
        raise HTTPException(status_code=400, detail=ADMIN_CREDIT_ERROR)
    meta = rec.metadata_json or {}
    if rec.purpose == "addon" and rec.addon_key:
        addon_service.activate_addon_for_user(db, user_id=rec.user_id, addon_key=rec.addon_key, payment_receipt_id=rec.id, source="manual_payment", price_paid_toman=paid_toman)
        logger.info("ADDON_RECEIPT_APPROVED admin_id=%s user_id=%s addon_key=%s", _, rec.user_id, rec.addon_key)
    elif meta.get("payment_type") == "subscription_renewal" and meta.get("plan"):
        subscription_service.renew_plan(db, rec.user, meta["plan"])
    elif meta.get("payment_type") == "plan_upgrade" and meta.get("target_plan") and meta.get("previous_expires_at"):
        subscription_service.apply_prorated_upgrade(db, rec.user, meta["target_plan"], datetime.fromisoformat(meta["previous_expires_at"]))
    else:
        wallet_service.credit(db, rec.user, coins, reason="manual_payment_approved", metadata={"receipt_id": rec.id, "admin_source": "web", "paid_toman": paid_toman, "toman_per_coin": TOMAN_PER_COIN, "approved_coins": coins}, idempotency_key=f"manual_receipt:{rec.id}:approval")
    rec.paid_toman = paid_toman; rec.amount_toman = paid_toman; rec.approved_coins = coins; rec.metadata_json = {**(rec.metadata_json or {}), "paid_toman": paid_toman, "toman_per_coin": TOMAN_PER_COIN, "approved_coins": coins}; rec.status = "approved"; rec.reviewed_at = datetime.utcnow(); rec.admin_note = str(form.get("note", "") or "")
    db.commit(); return RedirectResponse("/admin/receipts", status_code=303)

@router.post("/receipts/{receipt_id}/reject")
async def admin_reject_receipt(receipt_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    rec = db.get(PaymentReceipt, receipt_id)
    if rec and rec.status == "pending":
        form = await request.form(); rec.status = "rejected"; rec.reviewed_at = datetime.utcnow(); rec.admin_note = str(form.get("note", "رد") or "رد")
    db.commit(); return RedirectResponse("/admin/receipts", status_code=303)

@router.get("/stickers", response_class=HTMLResponse)
def admin_stickers(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    packs = db.scalars(select(StickerPack).order_by(StickerPack.created_at.desc())).all()
    items = db.scalars(select(StickerItem).order_by(StickerItem.created_at.desc()).limit(500)).all()
    return templates.TemplateResponse(request, "admin/stickers.html", {"packs": packs, "items": items, "categories": ["normal", "romantic", "playful", "adult_intimacy"], "genders": ["neutral", "female", "male"]})

@router.post("/stickers/packs")
async def admin_add_pack(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form(); name = str(form.get("name") or form.get("telegram_set_name") or "Pack"); set_name = str(form.get("telegram_set_name") or "")
    if set_name: db.add(StickerPack(name=name, telegram_set_name=set_name, description=str(form.get("description") or "")))
    db.commit(); return RedirectResponse("/admin/stickers", status_code=303)

@router.post("/stickers/items")
async def admin_add_sticker_item(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form(); fid = str(form.get("telegram_file_id") or "")
    if fid:
        triggers = [x.strip() for x in str(form.get("trigger_emojis") or form.get("emoji") or "").replace(",", " ").split() if x.strip()]
        stages = [x.strip().upper() for x in str(form.get("relationship_stages") or "").replace(",", " ").split() if x.strip()] or None
        db.add(StickerItem(
            pack_id=int(form.get("pack_id") or 0) or None,
            telegram_file_id=fid,
            emoji=str(form.get("emoji") or "") or (triggers[0] if triggers else None),
            label=str(form.get("label") or form.get("key") or "sticker"),
            usage_context=str(form.get("usage_context") or form.get("mood") or "comfort"),
            relationship_stage_min=str(form.get("relationship_stage_min") or "") or None,
            weight=int(form.get("weight") or 1),
            is_active=bool(form.get("is_active")),
            key=str(form.get("key") or "") or None,
            category=str(form.get("category") or "normal"),
            meaning=str(form.get("meaning") or "") or None,
            trigger_emojis=triggers or None,
            mood=str(form.get("mood") or "") or None,
            gender_target=str(form.get("gender_target") or "neutral"),
            relationship_stages=stages,
            enabled=bool(form.get("enabled")),
            probability=float(form.get("probability") or 1),
            daily_limit=int(form.get("daily_limit")) if str(form.get("daily_limit") or "").strip() else None,
        ))
    db.commit(); return RedirectResponse("/admin/stickers", status_code=303)


@router.post("/stickers/packs/{pack_id}/toggle")
def admin_toggle_pack(pack_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    p = db.get(StickerPack, pack_id)
    if p:
        p.is_active = not p.is_active
    db.commit()
    return RedirectResponse("/admin/stickers", status_code=303)


@router.post("/stickers/packs/{pack_id}/delete")
def admin_delete_pack(pack_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    p = db.get(StickerPack, pack_id)
    if p:
        for item in list(p.items or []):
            item.pack_id = None
        db.delete(p)
    db.commit()
    return RedirectResponse("/admin/stickers", status_code=303)


@router.post("/stickers/items/{item_id}/toggle")
def admin_toggle_item(item_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    i = db.get(StickerItem, item_id)
    if i:
        i.is_active = not i.is_active
        i.enabled = i.is_active
    db.commit()
    return RedirectResponse("/admin/stickers", status_code=303)


def _admin_space_list(value) -> list[str] | None:
    vals = [x.strip() for x in str(value or "").replace(",", " ").split() if x.strip()]
    return vals or None


@router.post("/stickers/items/{item_id}/edit")
async def admin_edit_sticker_item(item_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    item = db.get(StickerItem, item_id)
    if item:
        form = await request.form()
        item.pack_id = int(form.get("pack_id") or 0) or None
        item.key = str(form.get("key") or "").strip() or None
        item.label = str(form.get("label") or item.label or item.key or "sticker").strip()
        item.category = str(form.get("category") or "normal").strip()
        item.meaning = str(form.get("meaning") or "").strip() or None
        item.trigger_emojis = _admin_space_list(form.get("trigger_emojis"))
        item.emoji = item.trigger_emojis[0] if item.trigger_emojis else item.emoji
        item.gender_target = str(form.get("gender_target") or "neutral").strip()
        item.mood = str(form.get("mood") or "").strip() or None
        item.usage_context = str(form.get("usage_context") or item.mood or item.category or "comfort").strip()
        item.relationship_stages = [x.upper() for x in (_admin_space_list(form.get("relationship_stages")) or [])] or None
        item.relationship_stage_min = str(form.get("relationship_stage_min") or "").strip() or None
        try:
            item.probability = max(0.0, min(1.0, float(form.get("probability") or 1)))
        except Exception:
            item.probability = 1.0
        try:
            raw_daily = str(form.get("daily_limit") or "").strip()
            item.daily_limit = int(raw_daily) if raw_daily else None
        except Exception:
            item.daily_limit = None
        try:
            item.weight = max(1, int(form.get("weight") or 1))
        except Exception:
            item.weight = 1
        item.enabled = bool(form.get("enabled"))
        item.is_active = bool(form.get("is_active"))
    db.commit()
    return RedirectResponse("/admin/stickers", status_code=303)


@router.post("/stickers/items/{item_id}/delete")
def admin_delete_sticker_item(item_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    item = db.get(StickerItem, item_id)
    if item:
        db.delete(item)
    db.commit()
    return RedirectResponse("/admin/stickers", status_code=303)




def _range(range_name: str) -> tuple[datetime, datetime, list[str]]:
    now = datetime.utcnow()
    today = datetime(now.year, now.month, now.day)
    if range_name == "today":
        start, end = today, today + timedelta(days=1)
    elif range_name == "7d":
        start, end = today - timedelta(days=6), today + timedelta(days=1)
    elif range_name == "month":
        start, end = datetime(now.year, now.month, 1), today + timedelta(days=1)
    elif range_name == "prev_month":
        first = datetime(now.year, now.month, 1)
        last_prev = first - timedelta(days=1)
        start, end = datetime(last_prev.year, last_prev.month, 1), first
    else:
        start, end = today - timedelta(days=29), today + timedelta(days=1)
    labels = [(start + timedelta(days=i)).date().isoformat() for i in range(max((end.date() - start.date()).days, 1))]
    return start, end, labels


def _series(db: Session, date_col, value_col, *filters, start: datetime, end: datetime) -> dict[str, int]:
    day = func.date(date_col)
    rows = db.execute(select(day, value_col).where(date_col >= start, date_col < end, *filters).group_by(day).order_by(day)).all()
    return {str(row[0]): int(row[1] or 0) for row in rows if row[0] is not None}


def _event_series(db: Session, event_type: str, start: datetime, end: datetime) -> dict[str, int]:
    return _series(db, AnalyticsEvent.event_date, func.count(AnalyticsEvent.id), AnalyticsEvent.event_type == event_type, start=start, end=end)


def _usage_series(db: Session, col, start_date: date, end_date: date) -> dict[str, int]:
    rows = db.execute(select(DailyUsage.date, func.coalesce(func.sum(col), 0)).where(DailyUsage.date >= start_date, DailyUsage.date < end_date).group_by(DailyUsage.date)).all()
    return {row[0].isoformat(): int(row[1] or 0) for row in rows}


def _align(labels: list[str], values: dict[str, int]) -> list[int]:
    return [int(values.get(label, 0)) for label in labels]


def _analytics_overview(db: Session, range_name: str) -> dict:
    start, end, labels = _range(range_name)
    today = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    def approved_sum(start_dt):
        return int(db.scalar(select(func.coalesce(func.sum(PaymentReceipt.amount_toman), 0)).where(PaymentReceipt.status == "approved", PaymentReceipt.created_at >= start_dt)) or 0)
    total_users = db.scalar(select(func.count(User.id))) or 0
    paid_users = db.scalar(select(func.count(func.distinct(Subscription.user_id))).where(Subscription.status == "active", Subscription.plan.notin_(["free", "daily"]))) or 0
    active_today = db.scalar(select(func.count(func.distinct(Message.user_id))).where(Message.created_at >= datetime.combine(today, datetime.min.time()))) or 0
    rel_avg = db.scalar(select(func.avg((Relationship.intimacy + Relationship.trust + Relationship.attachment + Relationship.attraction) / 4))) or 0
    kpis = [
        {"label": "درآمد امروز", "value": approved_sum(datetime.combine(today, datetime.min.time())), "suffix": " تومان", "trend": "neutral"},
        {"label": "درآمد این هفته", "value": approved_sum(datetime.combine(week_start, datetime.min.time())), "suffix": " تومان", "trend": "up"},
        {"label": "درآمد این ماه", "value": approved_sum(datetime.combine(month_start, datetime.min.time())), "suffix": " تومان", "trend": "up"},
        {"label": "کاربران جدید امروز", "value": db.scalar(select(func.count(User.id)).where(User.created_at >= datetime.combine(today, datetime.min.time()))) or 0, "trend": "neutral"},
        {"label": "کاربران فعال امروز", "value": active_today, "trend": "neutral"},
        {"label": "نرخ تبدیل رایگان به پرداختی", "value": round((paid_users / total_users) * 100, 2) if total_users else 0, "suffix": "%", "trend": "neutral"},
        {"label": "پرداخت‌های در انتظار بررسی", "value": db.scalar(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "pending")) or 0, "trend": "down"},
        {"label": "کاربران VIP/Plus فعال", "value": db.scalar(select(func.count(func.distinct(Subscription.user_id))).where(Subscription.status == "active", Subscription.plan.in_(["vip", "plus"]))) or 0, "trend": "up"},
        {"label": "پیام‌های امروز", "value": db.scalar(select(func.count(Message.id)).where(Message.created_at >= datetime.combine(today, datetime.min.time()))) or 0, "trend": "neutral"},
        {"label": "وویس‌های امروز", "value": db.scalar(select(func.coalesce(func.sum(DailyUsage.daily_voice_sent), 0)).where(DailyUsage.date == today)) or 0, "trend": "neutral"},
        {"label": "پیام‌های خودجوش ارسال‌شده", "value": db.scalar(select(func.count(ProactiveMessage.id)).where(ProactiveMessage.sent_at >= start, ProactiveMessage.sent_at < end)) or 0, "trend": "neutral"},
        {"label": "میانگین عمق رابطه", "value": round(float(rel_avg), 2), "trend": "up"},
    ]
    return {"range": range_name, "labels": labels, "kpis": kpis, "pending_receipts": _pending_receipts(db), "recent_users": _recent_users(db)}


def _payment_funnel(db: Session, start: datetime, end: datetime) -> dict:
    rows = db.execute(select(PaymentReceipt.status, func.count(PaymentReceipt.id)).where(PaymentReceipt.created_at >= start, PaymentReceipt.created_at < end).group_by(PaymentReceipt.status)).all()
    counts = {status: int(count) for status, count in rows}
    approved = counts.get("approved", 0); total = sum(counts.values())
    reviewed_rows = db.scalars(select(PaymentReceipt).where(PaymentReceipt.reviewed_at.is_not(None), PaymentReceipt.created_at >= start, PaymentReceipt.created_at < end).limit(1000)).all()
    avg_minutes = 0
    if reviewed_rows:
        avg_minutes = sum((r.reviewed_at - r.created_at).total_seconds() / 60 for r in reviewed_rows if r.reviewed_at) / len(reviewed_rows)
    return {"pending": counts.get("pending", 0), "approved": approved, "rejected": counts.get("rejected", 0), "approval_rate": round((approved / total) * 100, 2) if total else 0, "avg_approval_minutes": round(float(avg_minutes), 2)}


def _revenue_by_plan(db: Session, start: datetime, end: datetime) -> dict[str, int]:
    rows = db.scalars(select(PaymentReceipt).where(PaymentReceipt.status == "approved", PaymentReceipt.created_at >= start, PaymentReceipt.created_at < end)).all()
    out = {"mini": 0, "basic": 0, "plus": 0, "vip": 0, "other": 0}
    for r in rows:
        meta = r.metadata_json or {}; plan = (meta.get("target_plan") or meta.get("plan") or "other").lower()
        out[plan if plan in out else "other"] += int(r.amount_toman or 0)
    return out


def _plan_distribution(db: Session) -> dict[str, int]:
    rows = db.execute(select(Subscription.plan, func.count(func.distinct(Subscription.user_id))).where(Subscription.status == "active").group_by(Subscription.plan)).all()
    out = {"free": 0, "daily": 0, "mini": 0, "basic": 0, "plus": 0, "vip": 0}
    for plan, count in rows:
        out[(plan or "free").lower()] = int(count or 0)
    users = db.scalar(select(func.count(User.id))) or 0
    assigned = sum(out.values())
    out["free"] += max(users - assigned, 0)
    return out


def _mrr(db: Session) -> dict:
    dist = _plan_distribution(db); plan_prices={k:v.price_coins for k,v in get_plan_configs().items()}; mrr = sum(plan_prices.get(plan, 0) * count for plan, count in dist.items())
    paid = sum(count for plan, count in dist.items() if plan not in {"free", "daily"}); users = db.scalar(select(func.count(User.id))) or 0
    return {"estimated_mrr": mrr, "paid_user_count": paid, "arppu": round(mrr / paid, 2) if paid else 0, "arpu": round(mrr / users, 2) if users else 0}


def _expiring(db: Session) -> dict:
    now = datetime.utcnow()
    return {"next_3_days": db.scalar(select(func.count(Subscription.id)).where(Subscription.status == "active", Subscription.expires_at >= now, Subscription.expires_at < now + timedelta(days=3))) or 0, "next_7_days": db.scalar(select(func.count(Subscription.id)).where(Subscription.status == "active", Subscription.expires_at >= now, Subscription.expires_at < now + timedelta(days=7))) or 0, "recently_expired": db.scalar(select(func.count(Subscription.id)).where(Subscription.expires_at >= now - timedelta(days=7), Subscription.expires_at < now)) or 0}


def _retention_summary(db: Session, start: datetime, end: datetime) -> dict:
    cohort = db.scalars(select(User).where(User.created_at >= start, User.created_at < end)).all(); total = len(cohort)
    return {"created": total, "next_day": sum(1 for u in cohort if u.last_seen_at >= u.created_at + timedelta(days=1)), "day_7": sum(1 for u in cohort if u.last_seen_at >= u.created_at + timedelta(days=7)), "day_30": sum(1 for u in cohort if u.last_seen_at >= u.created_at + timedelta(days=30)), "label": "retention estimate"}


def _delivery_mix(db: Session, start: datetime, end: datetime) -> dict[str, int]:
    rows = db.execute(select(User.last_delivery_type, func.count(User.id)).where(User.last_seen_at >= start, User.last_seen_at < end).group_by(User.last_delivery_type)).all()
    return {str(k or "text"): int(v or 0) for k, v in rows}


def _token_series(db: Session, labels: list[str], start_date: date, end_date: date) -> dict:
    rows = db.execute(select(DailyUsage.date, func.coalesce(func.sum(DailyUsage.input_tokens), 0), func.coalesce(func.sum(DailyUsage.output_tokens), 0)).where(DailyUsage.date >= start_date, DailyUsage.date < end_date).group_by(DailyUsage.date)).all()
    inp = {r[0].isoformat(): int(r[1] or 0) for r in rows}; out = {r[0].isoformat(): int(r[2] or 0) for r in rows}
    return {"input": _align(labels, inp), "output": _align(labels, out)}


def _partner_analytics(db: Session) -> dict:
    def counts(col):
        return {str(k or "unknown"): int(v or 0) for k, v in db.execute(select(col, func.count(User.id)).group_by(col)).all()}
    stages = {str(k or "STRANGER"): int(v or 0) for k, v in db.execute(select(Relationship.stage, func.count(Relationship.id)).group_by(Relationship.stage)).all()}
    moods = counts(User.current_mood)
    depth = db.execute(select(func.avg(Relationship.intimacy), func.avg(Relationship.trust), func.avg(Relationship.attachment), func.avg(Relationship.attraction))).one()
    return {"partner_gender": counts(User.partner_gender), "personality": counts(User.partner_personality_type), "relationship_stage": stages, "mood": moods, "depth": {"intimacy": round(float(depth[0] or 0), 2), "trust": round(float(depth[1] or 0), 2), "attachment": round(float(depth[2] or 0), 2), "attraction": round(float(depth[3] or 0), 2)}, "funnel": {"gender_selected": db.scalar(select(func.count(User.id)).where(User.partner_gender.is_not(None))) or 0, "name_selected": db.scalar(select(func.count(User.id)).where(User.partner_name.is_not(None))) or 0, "onboarding_complete": db.scalar(select(func.count(User.id)).where(User.onboarding_step == "complete")) or 0, "first_chat_sent": db.scalar(select(func.count(func.distinct(Message.user_id)))) or 0}}


def _pending_receipts(db: Session):
    return db.scalars(select(PaymentReceipt).where(PaymentReceipt.status == "pending").order_by(PaymentReceipt.created_at.desc()).limit(5)).all()


def _recent_users(db: Session):
    return db.scalars(select(User).order_by(User.created_at.desc()).limit(6)).all()


def _latest_user_message(db: Session, user_id: int) -> str | None:
    row = db.scalar(select(Message).where(Message.user_id == user_id, Message.role == "user").order_by(Message.created_at.desc()).limit(1))
    return row.content if row else None


def _situation_field(raw: str | None, key: str):
    try:
        import json
        return json.loads(raw or "{}").get(key, "—")
    except Exception:
        return "—"


def _usage_totals(db: Session, start_dt: datetime | None = None, user_id: int | None = None):
    filters=[]
    if start_dt: filters.append(AiUsageEvent.created_at >= start_dt)
    if user_id: filters.append(AiUsageEvent.user_id == user_id)
    stmt=select(func.coalesce(func.sum(AiUsageEvent.total_tokens),0), func.coalesce(func.sum(AiUsageEvent.input_tokens),0), func.coalesce(func.sum(AiUsageEvent.output_tokens),0), func.coalesce(func.sum(AiUsageEvent.cost_usd),0), func.coalesce(func.sum(AiUsageEvent.cost_toman),0), func.count(AiUsageEvent.id))
    if filters: stmt=stmt.where(and_(*filters))
    return db.execute(stmt).one()

@router.get("/usage", response_class=HTMLResponse)
def usage_page(request: Request, range: str = "30d", plan: str | None = None, feature: str | None = None, model: str | None = None, status: str | None = None, user_id: int | None = None, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    logger.info("ADMIN_DASHBOARD_USAGE_VIEW admin_id=%s", _)
    start_dt, end_dt, labels = _range(range)
    filters=[AiUsageEvent.created_at >= start_dt, AiUsageEvent.created_at < end_dt]
    if plan: filters.append(AiUsageEvent.plan == plan)
    if feature: filters.append(AiUsageEvent.feature == feature)
    if model: filters.append(AiUsageEvent.model == model)
    if status: filters.append(AiUsageEvent.status == status)
    if user_id: filters.append(AiUsageEvent.user_id == user_id)
    events=db.scalars(select(AiUsageEvent).where(and_(*filters)).order_by(AiUsageEvent.created_at.desc()).limit(200)).all()
    totals=db.execute(select(func.coalesce(func.sum(AiUsageEvent.total_tokens),0), func.coalesce(func.sum(AiUsageEvent.input_tokens),0), func.coalesce(func.sum(AiUsageEvent.output_tokens),0), func.coalesce(func.sum(AiUsageEvent.cost_usd),0), func.coalesce(func.sum(AiUsageEvent.cost_toman),0), func.count(AiUsageEvent.id)).where(and_(*filters))).one()
    by_model=db.execute(select(AiUsageEvent.model, func.coalesce(func.sum(AiUsageEvent.cost_usd),0), func.coalesce(func.sum(AiUsageEvent.total_tokens),0)).where(and_(*filters)).group_by(AiUsageEvent.model).order_by(func.sum(AiUsageEvent.cost_usd).desc()).limit(30)).all()
    by_feature=db.execute(select(AiUsageEvent.feature, func.coalesce(func.sum(AiUsageEvent.cost_usd),0), func.count(AiUsageEvent.id)).where(and_(*filters)).group_by(AiUsageEvent.feature).order_by(func.sum(AiUsageEvent.cost_usd).desc())).all()
    by_plan=db.execute(select(AiUsageEvent.plan, func.coalesce(func.sum(AiUsageEvent.cost_usd),0), func.count(AiUsageEvent.id)).where(and_(*filters)).group_by(AiUsageEvent.plan).order_by(func.sum(AiUsageEvent.cost_usd).desc())).all()
    by_user=db.execute(select(AiUsageEvent.user_id, func.coalesce(func.sum(AiUsageEvent.cost_usd),0), func.coalesce(func.sum(AiUsageEvent.total_tokens),0)).where(and_(*filters), AiUsageEvent.user_id.is_not(None)).group_by(AiUsageEvent.user_id).order_by(func.sum(AiUsageEvent.cost_usd).desc()).limit(20)).all()
    unpriced=db.execute(select(AiUsageEvent.model, AiUsageEvent.feature, func.count(AiUsageEvent.id)).where(and_(*filters), AiUsageEvent.cost_usd == 0, AiUsageEvent.total_tokens > 0).group_by(AiUsageEvent.model, AiUsageEvent.feature)).all()
    return templates.TemplateResponse(request, "admin/usage.html", {"range": range, "events": events, "totals": totals, "by_model": by_model, "by_feature": by_feature, "by_plan": by_plan, "by_user": by_user, "unpriced": unpriced, "filters": {"plan":plan or "", "feature":feature or "", "model":model or "", "status":status or "", "user_id":user_id or ""}})

@router.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    configs=get_plan_configs(); svc=SettingsService(); rows=[]; warning=False
    for code,cfg in configs.items():
        if code not in {"free","mini","basic","plus","vip"}: continue
        db_limit=svc.get_int(db, f"limits.{code}.daily_token_limit", cfg.daily_token_limit)
        warning = warning or db_limit != cfg.daily_token_limit
        pricing=estimate_llm_cost(model=svc.get_str(db,"llm.venice.model","qwen-3-6-plus"), input_tokens=int(db_limit*.7), output_tokens=int(db_limit*.3), db=db)
        rows.append({"code":code,"config":cfg,"db_limit":db_limit,"mismatch":db_limit != cfg.daily_token_limit,"daily_cost":pricing["cost_usd"],"monthly_cost":pricing["cost_usd"]*30})
    return templates.TemplateResponse(request, "admin/plans.html", {"rows": rows, "warning": warning})

@router.post("/plans/settings")
async def plans_save(request: Request, db: Session = Depends(get_db), admin: str = Depends(require_admin)) -> RedirectResponse:
    form=await request.form(); svc=SettingsService()
    for key,val in form.items():
        if key.startswith("limits.") or key.startswith("subscription."):
            old=svc.get(db,key,""); svc.set_value(db,key,val,"integer"); logger.info("ADMIN_PLAN_LIMIT_UPDATED admin_id=%s key=%s old=%s new=%s", admin, key, old, val)
    db.commit(); return RedirectResponse("/admin/plans", status_code=303)

@router.get("/models", response_class=HTMLResponse)
def models_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    keys=db.scalars(select(AppSetting).where((AppSetting.key.like("pricing.%")) | (AppSetting.key.in_(["billing.usd_to_toman","llm.venice.model","llm.primary_persian_model","VISION_MODEL","VISION_FALLBACK_MODEL","STT_MODEL","STT_FALLBACK_MODEL","TTS_MODEL"]))).order_by(AppSetting.key)).all()
    return templates.TemplateResponse(request, "admin/models.html", {"settings": keys})

@router.post("/models/settings")
async def models_save(request: Request, db: Session = Depends(get_db), admin: str = Depends(require_admin)) -> RedirectResponse:
    form=await request.form(); svc=SettingsService()
    for key,val in form.items():
        if key.startswith("pricing.") or key.startswith("billing.") or key.startswith("llm.") or key in {"VISION_MODEL","VISION_FALLBACK_MODEL","STT_MODEL","STT_FALLBACK_MODEL","TTS_MODEL"}:
            svc.set_value(db,key,val,"float" if key.startswith(("pricing.","billing.")) else "string"); logger.info("ADMIN_MODEL_PRICING_UPDATED admin_id=%s model=%s", admin, key)
    db.commit(); return RedirectResponse("/admin/models", status_code=303)

@router.get("/media", response_class=HTMLResponse)
def media_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    rows=db.execute(select(MediaMessage, User).outerjoin(User, MediaMessage.user_id == User.id).order_by(MediaMessage.created_at.desc()).limit(200)).all()
    counts=db.execute(select(MediaMessage.kind, MediaMessage.processing_status, func.count(MediaMessage.id)).group_by(MediaMessage.kind, MediaMessage.processing_status)).all()
    cost_by_media=db.execute(select(AiUsageEvent.feature, AiUsageEvent.model, func.coalesce(func.sum(AiUsageEvent.cost_usd),0)).where(AiUsageEvent.feature.in_(["vision","stt"])).group_by(AiUsageEvent.feature,AiUsageEvent.model)).all()
    return templates.TemplateResponse(request,"admin/media.html",{"rows":rows,"counts":counts,"cost_by_media":cost_by_media,"store_raw":get_settings().store_raw_user_images})

@router.get("/proactive", response_class=HTMLResponse)
def proactive_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    rows=db.scalars(select(ProactiveMessage).order_by(ProactiveMessage.created_at.desc()).limit(100)).all()
    return templates.TemplateResponse(request,"admin/proactive.html",{"rows":rows})

@router.get("/health", response_class=HTMLResponse)
def health_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    db_ok=True
    try: db.execute(select(func.count(User.id)).limit(1)).scalar()
    except Exception: db_ok=False
    return templates.TemplateResponse(request,"admin/health.html",{"db_ok":db_ok,"alembic":"use `alembic current` in container","filters":["ERROR","Traceback","TOKEN_USAGE","AI_USAGE","PHOTO_","VOICE_","ADDON_","PAYMENT_"]})

@router.get('/image-generation')
def image_generation_admin(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    from app.models.image_generation import ImageGenerationJob, ImageGenerationFeedback, ImageGenerationArtifact, PartnerVisualProfile
    from app.llm.image_client import venice_image_payload
    from app.services.image_prompt_engine import PROMPT_ENGINE_VERSION, IMAGE_ADDON_KEY
    product = db.scalar(select(AddonProduct).where(AddonProduct.key == IMAGE_ADDON_KEY))
    by_status = dict(db.execute(select(ImageGenerationJob.status, func.count(ImageGenerationJob.id)).group_by(ImageGenerationJob.status)).all())
    sent = by_status.get('sent', 0); failed = by_status.get('failed', 0); total = sum(by_status.values()) or 1
    feedback = dict(db.execute(select(ImageGenerationFeedback.rating, func.count(ImageGenerationFeedback.id)).group_by(ImageGenerationFeedback.rating)).all())
    prompts = db.scalars(select(ImageGenerationJob).where(ImageGenerationJob.prompt.is_not(None)).order_by(ImageGenerationJob.created_at.desc()).limit(20)).all()
    artifacts = db.scalar(select(func.count(ImageGenerationArtifact.id)).where(ImageGenerationArtifact.image_bytes.is_not(None))) or 0
    return {'addon_enabled': bool(product and product.is_active), 'unlock_price_coins': int(product.price_coins if product else 500), 'defaults': venice_image_payload('PROMPT','NEGATIVE'), 'prompt_engine_version': PROMPT_ENGINE_VERSION, 'jobs_by_status': by_status, 'success_rate': sent/total, 'failure_rate': failed/total, 'feedback_score': feedback, 'recent_prompts': [{'id':j.id,'user_id':j.user_id,'mode':j.content_mode,'prompt':j.prompt,'negative_prompt':j.negative_prompt} for j in prompts], 'artifact_cleanup_status': {'stored_artifacts': artifacts}}

@router.post('/image-generation/jobs/{job_id}/retry')
def retry_image_job(job_id: int, recovery: bool = Query(False), db: Session = Depends(get_db), _: str = Depends(require_admin)):
    from app.models.image_generation import ImageGenerationJob, ImageGenerationArtifact
    job = db.get(ImageGenerationJob, job_id)
    if not job: raise HTTPException(404, 'job not found')
    artifact = db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id == job.id))
    sent_without_message = job.status == 'sent' and not job.telegram_message_id
    if job.status == 'sent' and job.telegram_message_id and not recovery:
        raise HTTPException(409, 'sent job with telegram_message_id cannot be retried without recovery=true')
    mode = 'delivery_retry'
    if sent_without_message or recovery:
        mode = 'regeneration_recovery' if not (artifact and artifact.image_bytes) else 'delivery_recovery'
        meta = dict(job.metadata_json or {})
        meta.update({'recovery_reason': 'sent_without_telegram_delivery' if sent_without_message else 'operator_recovery', 'recovery_regeneration': mode == 'regeneration_recovery'})
        job.metadata_json = meta
    job.status = 'delivery_failed' if artifact and artifact.image_bytes else 'queued'
    job.failed_at=None; job.locked_at=None; job.lock_expires_at=None; job.error_code=None; job.error_message=None
    job.sent_at = None if not job.telegram_message_id else job.sent_at
    db.commit(); return {'ok': True, 'mode': mode, 'reuse_artifact': bool(artifact and artifact.image_bytes)}

@router.post('/users/{user_id}/visual-profile/reset')
def reset_visual_profile(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    from app.models.image_generation import PartnerVisualProfile
    p = db.scalar(select(PartnerVisualProfile).where(PartnerVisualProfile.user_id==user_id))
    if p: db.delete(p); db.commit()
    return {'ok': True}

@router.get('/generated-media', response_class=HTMLResponse)
def generated_media_page(request: Request, start: str='', end: str='', user: str='', media_kind: str='', status: str='', model: str='', content_mode: str='', feedback: str='', archive_status: str='', db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    from app.models.image_generation import ImageGenerationJob, ImageGenerationFeedback, GeneratedVoiceOutput
    img_stmt=select(ImageGenerationJob, User, ImageGenerationFeedback, UsageCharge).outerjoin(User, User.id==ImageGenerationJob.user_id).outerjoin(ImageGenerationFeedback, ImageGenerationFeedback.job_id==ImageGenerationJob.id).outerjoin(UsageCharge, UsageCharge.id==ImageGenerationJob.usage_charge_id)
    voice_stmt=select(GeneratedVoiceOutput, User, UsageCharge).outerjoin(User, User.id==GeneratedVoiceOutput.user_id).outerjoin(UsageCharge, UsageCharge.id==GeneratedVoiceOutput.usage_charge_id)
    def dt(v, end_of_day=False):
        if not v: return None
        d=datetime.fromisoformat(v)
        return d + timedelta(days=1) if end_of_day and len(v)==10 else d
    if start:
        img_stmt=img_stmt.where(ImageGenerationJob.created_at>=dt(start)); voice_stmt=voice_stmt.where(GeneratedVoiceOutput.created_at>=dt(start))
    if end:
        img_stmt=img_stmt.where(ImageGenerationJob.created_at<dt(end, True)); voice_stmt=voice_stmt.where(GeneratedVoiceOutput.created_at<dt(end, True))
    if user:
        if user.isdigit():
            uid=int(user); img_stmt=img_stmt.where((ImageGenerationJob.user_id==uid)|(User.telegram_id==uid)); voice_stmt=voice_stmt.where((GeneratedVoiceOutput.user_id==uid)|(User.telegram_id==uid))
    if status:
        img_stmt=img_stmt.where(ImageGenerationJob.status==status); voice_stmt=voice_stmt.where(GeneratedVoiceOutput.status==status)
    if model:
        img_stmt=img_stmt.where(ImageGenerationJob.model==model); voice_stmt=voice_stmt.where(GeneratedVoiceOutput.model==model)
    if content_mode: img_stmt=img_stmt.where(ImageGenerationJob.content_mode==content_mode)
    if feedback:
        img_stmt=img_stmt.where(ImageGenerationFeedback.rating==feedback); voice_stmt=voice_stmt.where(GeneratedVoiceOutput.feedback==feedback)
    if archive_status:
        img_stmt=img_stmt.where(ImageGenerationJob.archive_status==archive_status); voice_stmt=voice_stmt.where(GeneratedVoiceOutput.archive_status==archive_status)
    images=[] if media_kind=='voice' else db.execute(img_stmt.order_by(ImageGenerationJob.created_at.desc()).limit(100)).all()
    voices=[] if media_kind=='image' else db.execute(voice_stmt.order_by(GeneratedVoiceOutput.created_at.desc()).limit(100)).all()
    charges=[r[-1] for r in images+voices if r[-1] is not None]
    total=len(images)+len(voices); sent=sum(1 for r in images if r[0].status=='sent')+sum(1 for r in voices if r[0].status=='sent'); arch=sum(1 for r in images if r[0].archive_status=='sent')+sum(1 for r in voices if r[0].archive_status=='sent')
    positive=sum(1 for r in images if getattr(r[2],'rating',None)=='positive')+sum(1 for r in voices if r[0].feedback=='positive')
    failures={}
    for r in images+voices:
        code=getattr(r[0],'error_code',None)
        if code: failures[code]=failures.get(code,0)+1
    kpis={'total generated images': len(images), 'total generated voices': len(voices), 'user-delivery success rate': round(sent*100/total,2) if total else 0, 'archive success rate': round(arch*100/total,2) if total else 0, 'average charged coins': round(sum(c.charged_coins for c in charges)/len(charges),2) if charges else 0, 'total charged coins': sum(c.charged_coins for c in charges), 'total provider cost USD': sum(float(c.actual_cost_usd or 0) for c in charges), 'positive-feedback rate': round(positive*100/total,2) if total else 0, 'failures grouped by error code': failures}
    keys=['image_generation.adult_enabled','image_generation.soft_safety_enabled','generated_media.forward_enabled','generated_media.chat_id','generated_media.forward_images','generated_media.forward_voices','generated_media.fallback_to_support_media_chat_id']
    safety=db.scalars(select(AppSetting).where(AppSetting.key.in_(keys))).all()
    filters={'start':start,'end':end,'user':user,'media_kind':media_kind,'status':status,'model':model,'content_mode':content_mode,'feedback':feedback,'archive_status':archive_status}
    return templates.TemplateResponse(request, 'admin/generated_media.html', {'images':images,'voices':voices,'kpis':kpis,'safety_settings':safety,'filters':filters})

@router.post('/generated-media')
async def generated_media_settings_save(request: Request, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)) -> RedirectResponse:
    form=await request.form(); keys={'generated_media.forward_enabled':'boolean','generated_media.chat_id':'string','generated_media.forward_images':'boolean','generated_media.forward_voices':'boolean','generated_media.fallback_to_support_media_chat_id':'boolean','image_generation.adult_enabled':'boolean','image_generation.soft_safety_enabled':'boolean'}
    for key,typ in keys.items():
        if key in form:
            row=db.scalar(select(AppSetting).where(AppSetting.key==key)) or AppSetting(key=key,value='',value_type=typ); db.add(row)
            old=row.value; row.value=str(form.get(key,'')); row.value_type=typ; row.updated_by_admin_id=int(admin_id) if str(admin_id).isdigit() else None; row.updated_at=datetime.utcnow()
            logger.info('GENERATED_MEDIA_SETTING_UPDATED admin_id=%s key=%s old_value=%s new_value=%s timestamp=%s', admin_id, key, old, row.value, row.updated_at.isoformat())
    db.commit(); return RedirectResponse('/admin/generated-media', status_code=303)

@router.get('/generated-media/images/{job_id}/thumbnail')
def generated_image_thumbnail(job_id:int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    from fastapi.responses import Response
    from app.models.image_generation import ImageGenerationJob
    job=db.get(ImageGenerationJob, job_id)
    if not job or not job.thumbnail_bytes: raise HTTPException(status_code=404, detail='not_found')
    return Response(job.thumbnail_bytes, media_type=job.thumbnail_mime_type or 'image/jpeg')

@router.get('/generated-media/voices/{voice_id}/audio')
def generated_voice_audio(voice_id:int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    from fastapi.responses import Response
    from app.models.image_generation import GeneratedVoiceOutput
    v=db.get(GeneratedVoiceOutput, voice_id)
    if not v or not v.audio_bytes: raise HTTPException(status_code=404, detail='not_found')
    return Response(v.audio_bytes, media_type=v.mime_type or 'audio/ogg')
