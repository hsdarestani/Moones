import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import String, and_, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
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
from app.models.wallet import Wallet
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
from app.models.partner_life import PartnerLifeEvent
from app.models.human_delivery import HumanDeliveryJob
from app.models.media import MediaMessage
from app.services.partner_life_service import PartnerLifeService, get_or_create_today_event
from app.services.style_audit import run_persian_audit
from app.models.addon import AddonProduct, UserAddon
from app.services.addon_service import AddonService, INTIMACY_MAX_UNLOCK

router = APIRouter(prefix="/admin", tags=["admin"])
wallet_service = WalletService()
subscription_service = SubscriptionService()
addon_service = AddonService()
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    settings = get_settings()
    valid_user = secrets.compare_digest(credentials.username, settings.admin_user)
    valid_password = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (valid_user and valid_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials", headers={"WWW-Authenticate": "Basic"})
    return credentials.username



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


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, range: str = "30d", db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    users = db.execute(
        select(User, Relationship, Wallet, Subscription, DailyUsage, func.count(Message.id).label("total_messages"), func.max(Message.created_at).label("latest_message_at"))
        .outerjoin(Relationship, Relationship.user_id == User.id)
        .outerjoin(Wallet, Wallet.user_id == User.id)
        .outerjoin(Subscription, (Subscription.user_id == User.id) & (Subscription.status == "active"))
        .outerjoin(DailyUsage, (DailyUsage.user_id == User.id) & (DailyUsage.date == date.today()))
        .outerjoin(Message, Message.user_id == User.id)
        .group_by(User.id, Relationship.id, Wallet.id, Subscription.id, DailyUsage.id)
        .order_by(func.max(Message.created_at).desc().nullslast(), User.last_seen_at.desc())
    ).all()
    users = _with_last_activity(users)
    analytics = _analytics(db)
    overview = _analytics_overview(db, range)
    return templates.TemplateResponse(request, "admin/dashboard.html", {"users": users, "analytics": analytics, "overview": overview, "range": range})



@router.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    plan: str | None = None,
    stage: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    query = (
        select(User, Relationship, Wallet, Subscription, func.count(Message.id).label("total_messages"), func.max(Message.created_at).label("latest_message_at"))
        .outerjoin(Relationship, Relationship.user_id == User.id)
        .outerjoin(Wallet, Wallet.user_id == User.id)
        .outerjoin(Subscription, (Subscription.user_id == User.id) & (Subscription.status == "active"))
        .outerjoin(Message, Message.user_id == User.id)
        .group_by(User.id, Relationship.id, Wallet.id, Subscription.id)
        .order_by(func.max(Message.created_at).desc().nullslast(), User.last_seen_at.desc())
    )
    if q:
        query = query.where((User.display_name.ilike(f"%{q}%")) | (User.telegram_id.cast(String).ilike(f"%{q}%")))
    if stage:
        query = query.where(Relationship.stage == stage)
    if plan:
        query = query.where(Subscription.plan == plan)
    users = _with_last_activity(db.execute(query.limit(300)).all())
    return templates.TemplateResponse(request, "admin/users.html", {"users": users, "plan": plan or "", "stage": stage or "", "q": q or "", "stages": [s.value for s in RelationshipStage]})



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
    filters = [Message.user_id == user.id]
    if q:
        filters.append(Message.content.ilike(f"%{q}%"))
    if start:
        filters.append(Message.created_at >= datetime.fromisoformat(start))
    if end:
        filters.append(Message.created_at <= datetime.fromisoformat(end) + timedelta(days=1))
    messages = db.scalars(select(Message).where(and_(*filters)).order_by(Message.created_at.asc())).all()
    memories = db.scalars(select(MemoryItem).where(MemoryItem.user_id == user.id).order_by(MemoryItem.created_at.desc()).limit(25)).all()
    wallet = wallet_service.get_or_create_wallet(db, user)
    subscription = subscription_service.get_active_subscription(db, user) or subscription_service.ensure_free_subscription(db, user)
    usage = subscription_service.get_or_create_today_usage(db, user)
    receipts = db.scalars(select(PaymentReceipt).where(PaymentReceipt.user_id == user.id).order_by(PaymentReceipt.created_at.desc()).limit(20)).all()
    recent_proactive = db.scalars(select(ProactiveMessage).where(ProactiveMessage.user_id == user.id).order_by(ProactiveMessage.created_at.desc()).limit(5)).all()
    last_user_message = db.scalar(select(Message).where(Message.user_id == user.id, Message.role == "user").order_by(Message.created_at.desc()).limit(1))
    today_life_event = get_or_create_today_event(db, user)
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
    form = await request.form(); coins, error = parse_admin_credit_amount(form.get("coins", 0))
    if error:
        raise HTTPException(status_code=400, detail=ADMIN_CREDIT_ERROR)
    meta = rec.metadata_json or {}
    if rec.purpose == "addon" and rec.addon_key:
        addon_service.activate_addon_for_user(db, user_id=rec.user_id, addon_key=rec.addon_key, payment_receipt_id=rec.id, source="manual_payment", price_paid_toman=coins)
        logger.info("ADDON_RECEIPT_APPROVED admin_id=%s user_id=%s addon_key=%s", _, rec.user_id, rec.addon_key)
    elif meta.get("payment_type") == "plan_upgrade" and meta.get("target_plan") and meta.get("previous_expires_at"):
        subscription_service.apply_prorated_upgrade(db, rec.user, meta["target_plan"], datetime.fromisoformat(meta["previous_expires_at"]))
    else:
        wallet_service.credit(db, rec.user, coins, reason="manual_payment_approved", metadata={"receipt_id": rec.id, "admin_source": "web"})
    rec.status = "approved"; rec.reviewed_at = datetime.utcnow(); rec.admin_note = str(form.get("note", "") or "")
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
    items = db.scalars(select(StickerItem).order_by(StickerItem.created_at.desc()).limit(200)).all()
    return templates.TemplateResponse(request, "admin/stickers.html", {"packs": packs, "items": items})

@router.post("/stickers/packs")
async def admin_add_pack(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form(); name = str(form.get("name") or form.get("telegram_set_name") or "Pack"); set_name = str(form.get("telegram_set_name") or "")
    if set_name: db.add(StickerPack(name=name, telegram_set_name=set_name, description=str(form.get("description") or "")))
    db.commit(); return RedirectResponse("/admin/stickers", status_code=303)

@router.post("/stickers/items")
async def admin_add_sticker_item(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form(); fid = str(form.get("telegram_file_id") or "")
    if fid:
        db.add(StickerItem(pack_id=int(form.get("pack_id") or 0) or None, telegram_file_id=fid, emoji=str(form.get("emoji") or "") or None, label=str(form.get("label") or "sticker"), usage_context=str(form.get("usage_context") or "comfort"), relationship_stage_min=str(form.get("relationship_stage_min") or "") or None, weight=int(form.get("weight") or 1), is_active=bool(form.get("is_active", "on"))))
    db.commit(); return RedirectResponse("/admin/stickers", status_code=303)

@router.post("/stickers/packs/{pack_id}/toggle")
def admin_toggle_pack(pack_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    p = db.get(StickerPack, pack_id)
    if p: p.is_active = not p.is_active
    db.commit(); return RedirectResponse("/admin/stickers", status_code=303)

@router.post("/stickers/items/{item_id}/toggle")
def admin_toggle_item(item_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    i = db.get(StickerItem, item_id)
    if i: i.is_active = not i.is_active
    db.commit(); return RedirectResponse("/admin/stickers", status_code=303)



PLAN_PRICES = {"daily": 0, "free": 0, "mini": 49000, "basic": 99000, "plus": 199000, "vip": 399000}


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
    dist = _plan_distribution(db); mrr = sum(PLAN_PRICES.get(plan, 0) * count for plan, count in dist.items())
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
