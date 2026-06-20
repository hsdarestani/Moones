import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, select
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

router = APIRouter(prefix="/admin", tags=["admin"])
wallet_service = WalletService()
subscription_service = SubscriptionService()
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    settings = get_settings()
    valid_user = secrets.compare_digest(credentials.username, settings.admin_user)
    valid_password = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (valid_user and valid_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    users = db.execute(
        select(User, Relationship, Wallet, Subscription, DailyUsage, func.count(Message.id).label("total_messages"))
        .outerjoin(Relationship, Relationship.user_id == User.id)
        .outerjoin(Wallet, Wallet.user_id == User.id)
        .outerjoin(Subscription, (Subscription.user_id == User.id) & (Subscription.status == "active"))
        .outerjoin(DailyUsage, (DailyUsage.user_id == User.id) & (DailyUsage.date == date.today()))
        .outerjoin(Message, Message.user_id == User.id)
        .group_by(User.id, Relationship.id, Wallet.id, Subscription.id, DailyUsage.id)
        .order_by(User.last_seen_at.desc())
    ).all()
    analytics = _analytics(db)
    return templates.TemplateResponse(request, "admin/dashboard.html", {"users": users, "analytics": analytics})


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
    partner_profile = OnboardingService().partner_profile(user)
    generated_voice_profile = generate_voice_profile(partner_profile, state, memories)
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
    }
    return templates.TemplateResponse(
        request,
        "admin/user_detail.html",
        {"user": user, "state": state, "messages": messages, "memories": memories, "inspector": inspector, "stages": [stage.value for stage in RelationshipStage], "q": q or "", "start": start or "", "end": end or ""},
    )


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
    if meta.get("payment_type") == "plan_upgrade" and meta.get("target_plan") and meta.get("previous_expires_at"):
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


def _latest_user_message(db: Session, user_id: int) -> str | None:
    row = db.scalar(select(Message).where(Message.user_id == user_id, Message.role == "user").order_by(Message.created_at.desc()).limit(1))
    return row.content if row else None


def _situation_field(raw: str | None, key: str):
    try:
        import json
        return json.loads(raw or "{}").get(key, "—")
    except Exception:
        return "—"
