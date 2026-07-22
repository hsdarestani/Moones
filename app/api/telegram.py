from __future__ import annotations
import asyncio
import logging
import os
import random
import re
import time
import json
from contextlib import suppress
from datetime import datetime
from dataclasses import dataclass
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.db.session import get_db, SessionLocal
from app.engine.orchestrator import ConversationOrchestrator
from app.engine.simple_chat import handle_simple_chat, sanitize_final_response
from app.engine.delivery_decider import decide_delivery, mark_delivery
from app.llm.tts_client import TTSFailure, synthesize_voice, select_tts_voice
from app.engine.emotion_engine import detect_emotion
from app.engine.relationship_engine import ensure_relationship
from app.models.payment import PaymentReceipt
from app.models.sticker import StickerItem, StickerPack
from app.models.support import SupportMessage
from app.services.bot_menu_service import BotMenuService
from app.services.onboarding_service import OnboardingService
from app.services.telegram_service import TelegramService
from app.services.wallet_service import WalletService, ensure_signup_welcome_credit
from app.services.sticker_service import StickerService
from app.services.interaction_reliability import resolve_reply_context, interpret_sticker
from app.services.subscription_service import LIMIT_MESSAGE
from app.services.media_input_service import MediaInputService
from app.services.support_media_service import forward_photo_to_support
from app.llm.vision_client import analyze_image_with_venice
from app.llm.stt_client import transcribe_audio_with_venice
from app.services.credit_validation import ADMIN_CREDIT_ERROR, parse_admin_credit_amount
from app.services.addon_service import AddonService, INTIMACY_MAX_UNLOCK, IMAGE_GENERATION_UNLOCK
from app.services.image_prompt_engine import ImageRouteDecision
from app.services.image_generation_service import enqueue_image_request, ImageGenerationDenied, store_feedback
from app.services.image_pipeline_v2_flags import resolve_image_pipeline_v2_flags
from app.services.semantic_image_router_flags import resolve_semantic_router_flags
from app.services.semantic_image_router_context import build_semantic_image_router_context
from app.services.semantic_image_intent_router import (SemanticImageDecision, SemanticImageIntentRouter,
    VeniceSemanticImageIntentModel, SemanticImageAction, canonical_explicit_image_action,
    mark_image_clarification_resolved, resolve_pending_image_clarification,
    enforce_clear_image_request_action, enforce_clarification_scope,
    enforce_referenced_object_request, enforce_partner_photo_defaults, supersede_pending_image_clarification,
    resolve_active_image_job_followup_semantically, should_report_active_job_instead_of_enqueuing,
    validate_source_reference_deterministically)
from app.services.generated_voice_service import (persist_and_deliver_voice, store_voice_feedback,
                                                   capture_voice_feedback, load_voice_feedback_profile)
from app.services.forward_batch_service import (ForwardBatchService, compact_forward_item,
                                                format_forward_batch, is_forwarded_message)
from redis.asyncio import Redis
from app.services.addon_upsell_service import detect_addon_opportunity, record_addon_upsell_event
from app.services.proactive_service import ProactiveService
from app.services.soft_upsell_service import SoftUpsellService
from app.services.bot_link_service import management_bot_url, management_bot_keyboard
from app.services.human_presence_engine import HumanPresenceEngine
from app.services.audio_transcription_service import AudioTranscriptionService, STTNotConfigured
from app.services.coin_pricing_service import CoinPricingService
from app.services.usage_billing_service import UsageBillingService, InsufficientCoins
from app.services.delayed_reaction_service import DelayedReactionService
from app.services.outbound_text_policy import sanitize_user_facing_text
from app.services.low_wallet_service import feature_insufficient_text, recharge_keyboard, should_send_low_wallet_notice
from app.models.message import Message
from sqlalchemy import select, func
from app.models.image_generation import GeneratedVoiceOutput

logger=logging.getLogger(__name__); router=APIRouter(prefix="/telegram", tags=["telegram"])

def _persian_digits(value: int) -> str:
 trans = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
 return str(value).translate(trans)
orchestrator=ConversationOrchestrator(); onboarding=OnboardingService(); menus=BotMenuService(); wallets=WalletService(); stickers=StickerService(); soft_upsells=SoftUpsellService(); human_presence=HumanPresenceEngine(); delayed_reactions=DelayedReactionService(); media_inputs=MediaInputService(); addons=AddonService()
forward_batches = ForwardBatchService(Redis.from_url(get_settings().redis_url, decode_responses=True))
_forward_tasks: set[asyncio.Task] = set()


async def _process_forward_items(bot_type: str, original: "TelegramUpdate", items: list[dict]) -> None:
    payload = original.model_dump(by_alias=True)
    payload["update_id"] = max(item["update_id"] for item in items)
    message = payload["message"]
    message["message_id"] = max(item["message_id"] for item in items)
    message["text"] = format_forward_batch(items); message["caption"] = None
    for field in ("photo", "voice", "audio", "sticker", "document", "forward_origin",
                  "forward_from", "forward_from_chat", "forward_sender_name", "forward_date"):
        message[field] = None
    with SessionLocal() as batch_db:
        await _handle(TelegramUpdate.model_validate(payload), batch_db, bot_type)

def _image_generation_denial_message(reason: str) -> str | None:
    return {
        "image_action_ambiguous": "این رو به‌صورت عکس جدید بسازم یا عکس قبلی رو تغییر بدم؟",
        "image_source_ambiguous": "منظورت کدوم عکس قبلیه؟ روی همون پیام ریپلای کن.",
        "image_composition_conflict": "توی تصویر فقط خودت باشی یا شخص دیگه‌ای هم کنارت باشه؟",
        "image_safety_detail_ambiguous": "منظورت شخصیت‌های داستانی بزرگسال هستند؟",
        "image_parser_uncertain": "درخواست عکست رو گرفتم، ولی یک تصمیم لازم برام روشن نبود. دقیق‌تر بگو عکس جدید می‌خوای یا تغییر عکس قبلی؟",
    }.get(reason)



async def _enqueue_and_acknowledge_image_request(
    *,
    db,
    user,
    chat_id,
    message_id,
    user_text,
    effective_request_text,
    route_decision,
    telegram_service,
    resolved_image_request=None,
    pending_resolution=None,
):
    try:
        enqueue_kwargs = {
            "user": user,
            "chat_id": chat_id,
            "source_telegram_message_id": message_id,
            "user_request": effective_request_text,
            "route_decision": route_decision,
        }
        if resolved_image_request is not None:
            enqueue_kwargs.update({
                "resolved_action": getattr(resolved_image_request, "action", None),
                "resolved_visual_intent": getattr(resolved_image_request, "effective_visual_intent", None),
                "clarification_resolved": True,
            })
        job = enqueue_image_request(db, **enqueue_kwargs)
        if pending_resolution is not None:
            mark_image_clarification_resolved(pending_resolution, telegram_message_id=message_id)
            logger.info("IMAGE_CLARIFICATION_RESOLUTION_APPLIED user_id=%s job_id=%s request_chain_id=%s action=%s framing=%s reason_code=%s", user.id, getattr(job, "id", None), getattr(resolved_image_request, "request_chain_id", None), getattr(pending_resolution, "action", None), None, getattr(resolved_image_request, "resolution_reason", "pending_clarification_answer"))
        db.commit()
        await _send_user_text(
            telegram_service,
            chat_id,
            __import__('app.services.partner_photo_contract', fromlist=['image_acknowledgement']).image_acknowledgement(getattr(job, 'metadata_json', None)),
            user_id=user.id,
            surface="chat",
            user_text=user_text,
        )
        return {"ok": True}
    except ImageGenerationDenied as exc:
        reason = str(exc)
        if reason == "addon_required":
            url = management_bot_url("addon_image_generation_unlock")
            await _send_user_text(
                telegram_service,
                chat_id,
                "برای دریافت عکس از مونس، اول افزودنی «دریافت عکس از مونس» رو از ربات مدیریت فعال کن. هزینه هر عکس جداگانه با سکه کم می‌شه.",
                user_id=user.id,
                surface="chat",
                user_text=user_text,
                reply_markup={"inline_keyboard": [[{"text": "فعال‌کردن دریافت عکس 🌙", "url": url}]]},
            )
        elif _image_generation_denial_message(reason):
            await _send_user_text(telegram_service, chat_id, _image_generation_denial_message(reason), user_id=user.id, surface="chat", user_text=user_text)
        elif reason in {"adult_image_addon_required", "adult_image_addon_disabled", "adult_generation_globally_disabled", "partner_under_21_or_ambiguous"}:
            start = "addon_adult_image_generation_unlock"
            url = management_bot_url(start)
            messages = {
                "adult_image_addon_required": "برای تصویر بزرگسالِ داستانی باید افزودنی «تصاویر بزرگسال مونس» رو از ربات مدیریت فعال کنی. افزودنی دریافت عکس مونس هم لازمه.",
                "adult_image_addon_disabled": "افزودنی تصاویر بزرگسال مونس رو قبلاً خریدی، ولی الان خاموشه. از ربات مدیریت می‌تونی بدون خرید دوباره روشنش کنی.",
                "adult_generation_globally_disabled": "در حال حاضر ارسال تصاویر بزرگسال توسط مدیریت مونس غیرفعاله.",
                "partner_under_21_or_ambiguous": "برای تصویر بزرگسال، سن پروفایل داستانی پارتنر باید مشخصاً ۲۱ سال یا بیشتر باشه.",
            }
            await _send_user_text(telegram_service, chat_id, messages[reason], user_id=user.id, surface="chat", user_text=user_text, reply_markup={"inline_keyboard": [[{"text": "مدیریت تصاویر بزرگسال 🌙", "url": url}]]})
        else:
            await _send_user_text(telegram_service, chat_id, "این بار نتونستم عکس رو درست آماده کنم؛ همون چیزی که می‌خوای رو دوباره بگو تا از نو بگیرمش.", user_id=user.id, surface="chat", user_text=user_text)
        db.commit()
        return {"ok": True}
    except InsufficientCoins as exc:
        if should_send_low_wallet_notice(db, user_id=user.id, feature="image_generation_bundle", dedupe_key=f"image:{message_id}"):
            await _send_user_text(telegram_service, chat_id, feature_insufficient_text("image_generation_bundle", balance=exc.balance, required=exc.required), user_id=user.id, surface="chat", user_text=user_text, reply_markup=recharge_keyboard())
        db.commit()
        return {"ok": True}
    except Exception as exc:
        db.rollback()
        logger.exception(
            "IMAGE_REQUEST_FAILED user_id=%s error_type=%s error_detail=%s",
            user.id,
            type(exc).__name__,
            str(exc)[:300],
        )
        await _send_user_text(telegram_service, chat_id, "الان نتونستم درخواست عکس رو ثبت کنم. چند دقیقه دیگه دوباره امتحان کن.", user_id=user.id, surface="chat", user_text=user_text)
        return {"ok": True}


def _schedule_forward_flush(key: str, bot_type: str, update: "TelegramUpdate", *, immediate: bool = False) -> None:
    async def callback(items):
        try:
            await _process_forward_items(bot_type, update, items)
        except Exception:
            if update.message:
                await TelegramService(bot_type).send_message(update.message.chat.id, FALLBACK_ERROR_TEXT)
            raise
    task = asyncio.create_task(forward_batches.flush(key, callback) if immediate else
                               forward_batches.flush_after_quiet(key, callback))
    _forward_tasks.add(task); task.add_done_callback(_forward_tasks.discard)




def _image_status_text(job_summary):
    if not job_summary: return None
    from app.services.partner_photo_contract import image_status_text
    return image_status_text(getattr(job_summary, 'status', None), getattr(job_summary, 'error_code', None))

def _semantic_decision_to_legacy_route(decision, recent_img):
    mapping={
        SemanticImageAction.GENERATE_NEW: 'semantic_generate_new',
        SemanticImageAction.REFINE_PREVIOUS: 'semantic_refine_previous',
        SemanticImageAction.VARIATION: 'semantic_variation',
        SemanticImageAction.RESEND_EXACT: 'semantic_resend_exact',
    }
    route=mapping.get(decision.action, 'chat')
    source_id=getattr(getattr(decision, 'source_reference', None), 'job_id', None) or getattr(recent_img, 'id', None)
    rd=ImageRouteDecision(route=route, explicit_image_request=route!='chat', contextual_followup=route not in {'chat','semantic_generate_new'}, recent_image_context_found=bool(recent_img), source_image_job_id=source_id, confidence=decision.confidence, reason_code='semantic_'+str(decision.reason_code))
    rd.semantic_decision=decision
    return rd

def _should_force_text_delivery(meta: dict | None) -> bool:
    meta = meta or {}
    if meta.get("user_move_intent") in {"confusion_or_annoyed", "style_correction", "continue_plain", "casual_reopen"}:
        return True
    if meta.get("natural_style_guard_rewrite") or meta.get("natural_style_guard_fallback") or meta.get("style_meta_talk_guard_applied"):
        return True
    if meta.get("emotional_loop_guard_applied") and meta.get("deterministic_repair_used"):
        return True
    return bool(meta.get("disable_human_extras"))

FALLBACK_ERROR_TEXT="یه مشکلی پیش اومد 😅\nدوباره امتحان کن، من اینجام."
VISION_ESTIMATED_INPUT_TOKENS = 1200
VISION_ESTIMATED_OUTPUT_TOKENS = 700


def _log_image_v2_route_shadow_if_enabled(db: Session, *, text: str, source_message_id: int | None, legacy_route: str) -> bool:
    image_v2_flags = resolve_image_pipeline_v2_flags(db)
    if not image_v2_flags.shadow_enabled:
        return False
    try:
        from app.services import image_pipeline_v2 as v2
        route_shadow = v2.route_shadow_decision(text, source_message_id=source_message_id, legacy_route=legacy_route)
        compact_keys = {
            'request_hash', 'source_message_id', 'legacy_route', 'v2_is_image_request',
            'v2_detected_action', 'route_mismatch', 'fallback_required', 'policy_reason_code',
        }
        compact_shadow = {k: route_shadow[k] for k in compact_keys if k in route_shadow}
        logger.info("IMAGE_V2_ROUTE_SHADOW %s", json.dumps(compact_shadow, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        logger.info("IMAGE_V2_ROUTE_SHADOW_FAILED source_message_id=%s error=%s", source_message_id, type(exc).__name__)
    return True


def _reserve_media_charge(db: Session, user, *, feature: str, model: str, quantity: int | float, key_suffix: str):
    pricing = CoinPricingService()
    quote = pricing.quote_unit(db, provider="venice", model=model, feature=feature, quantity=quantity)
    key = f"{feature}:{user.id}:{key_suffix}"
    charge = UsageBillingService().reserve(db, user=user, idempotency_key=key, feature=feature, provider="venice", model=model, quote=quote, correlation_id=key)
    return charge, quote

def _reserve_vision_charge(db: Session, user, *, model: str, estimated_input_tokens: int, estimated_output_tokens: int, key_suffix: str):
    pricing = CoinPricingService()
    quote = pricing.quote_tokens(
        db,
        provider="venice",
        model=model,
        feature="vision",
        input_tokens=estimated_input_tokens,
        output_tokens=estimated_output_tokens,
    )
    key = f"vision:{user.id}:{key_suffix}"
    charge = UsageBillingService().reserve(db, user=user, idempotency_key=key, feature="vision", provider="venice", model=model, quote=quote, correlation_id=key)
    return charge, quote

def _settle_media_charge(db: Session, charge, quote):
    return UsageBillingService().settle(db, charge=charge, actual_quote=quote)

def _refund_media_charge(db: Session, charge, exc: Exception):
    return UsageBillingService().refund(db, charge=charge, error=str(exc))
LIMITED_MEDIA_MESSAGE="فعلاً با متن کنارت می‌مونم 🌙"
FAIR_USE_MESSAGE="برای حفظ کیفیت تجربه، امروز یه کم آروم‌تر ادامه می‌دم. هنوز اینجام، فقط فعلاً بیشتر با متن جواب می‌دم 🌙"
REQUIRED_CHANNEL_MESSAGE="برای استفاده از مونس، اول عضو کانال آپدیت‌ها شو 🌙\n\nاونجا خبر قابلیت‌های جدید، آپدیت‌ها و هدیه‌ها رو می‌ذاریم."
REQUIRED_CHANNEL_RETRY="هنوز عضویتت تأیید نشده. اول عضو کانال شو، بعد دوباره بزن عضو شدم ✅"
FREE_PHOTO_UPGRADE_MESSAGE="عکستو گرفتم، ولی فعلاً دیدن عکس فعال نیست. بعداً دوباره امتحان کن."
FREE_VOICE_UPGRADE_MESSAGE="وویستو گرفتم، ولی فعلاً شنیدن وویس فعال نیست. اگه همونو بنویسی جواب می‌دم."
UPGRADE_INTENT_MESSAGE="برای باز کردن قابلیت‌های بیشتر مونس، از ربات مدیریت استفاده کن 🌙\nاونجا می‌تونی کیف پولت رو شارژ کنی و افزودنی‌ها رو ببینی."
class TelegramUser(BaseModel): id:int; first_name:str|None=None; username:str|None=None; language_code:str|None=None
class TelegramChat(BaseModel): id:int
class TelegramPhoto(BaseModel): file_id:str; file_unique_id:str|None=None; file_size:int|None=None; width:int|None=None; height:int|None=None
class TelegramDocument(BaseModel): file_id:str; file_unique_id:str|None=None; file_name:str|None=None; mime_type:str|None=None; file_size:int|None=None
class TelegramSticker(BaseModel): file_id:str; emoji:str|None=None; set_name:str|None=None
class TelegramAudio(BaseModel): file_id:str; file_unique_id:str|None=None; duration:int|None=None; mime_type:str|None=None; file_name:str|None=None; file_size:int|None=None
class TelegramVoice(BaseModel): file_id:str; file_unique_id:str|None=None; duration:int|None=None; mime_type:str|None=None; file_size:int|None=None
class TelegramMessage(BaseModel):
    message_id:int; from_user:TelegramUser=Field(alias="from"); chat:TelegramChat; text:str|None=None; caption:str|None=None; photo:list[TelegramPhoto]|None=None; document:TelegramDocument|None=None; sticker:TelegramSticker|None=None; voice:TelegramVoice|None=None; audio:TelegramAudio|None=None; reply_to_message:TelegramMessage|None=None
    forward_origin:dict|None=None; forward_from:TelegramUser|None=None; forward_from_chat:TelegramChat|None=None; forward_sender_name:str|None=None; forward_date:int|None=None
class TelegramCallbackQuery(BaseModel): id:str; from_user:TelegramUser=Field(alias="from"); message:TelegramMessage|None=None; data:str|None=None
class TelegramUpdate(BaseModel): update_id:int; message:TelegramMessage|None=None; callback_query:TelegramCallbackQuery|None=None


@dataclass
class CallbackResult:
    text: str | None
    markup: dict | None = None
    edit_original: bool = True
    answer_text: str | None = None

@router.post("/webhook")
@router.post("/management/webhook")
async def management_webhook(update:TelegramUpdate, request:Request, db:Session=Depends(get_db)): return await _handle(update,db,"management")
@router.post("/chat/webhook")
async def chat_webhook(update:TelegramUpdate, request:Request, db:Session=Depends(get_db)): return await _handle(update,db,"chat")

def _is_admin(tid:int)->bool: return tid in get_settings().admin_ids

def _required_channel_keyboard():
    settings=get_settings()
    return {"inline_keyboard":[[{"text":"عضویت در کانال MoonesAI","url":settings.required_channel_url}],[{"text":"عضو شدم ✅","callback_data":"check_required_channel"}]]}

def _management_bot_username() -> str:
    settings=get_settings()
    username=(settings.management_bot_username or settings.telegram_management_bot_username).lstrip("@")
    return f"@{username}"

def _management_bot_url() -> str:
    settings=get_settings()
    return settings.management_bot_url or f"https://t.me/{_management_bot_username().lstrip('@')}"

def _management_keyboard(text: str = "رفتن به ربات مدیریت 🌙") -> dict:
    return {"inline_keyboard":[[{"text":text,"url":_management_bot_url()}]]}

def is_upgrade_or_feature_unlock_intent(text: str) -> bool:
    normalized=(text or "").strip().replace("\u200c"," ")
    lowered=normalized.lower()
    if not lowered:
        return False
    strong_phrases=[
        "چطور باز کنم","چطور فعال کنم","چجوری فعال کنم","چطوری فعال کنم","چطور بخرم","چجوری بخرم","چطوری بخرم",
        "افزودن موجودی","قابلیت بیشتر","قابلیتاش بیشتر","عکس باز شه","عکس فعال شه",
        "وویس فعال شه","چطور عکس بفرستم","چرا عکس نمیبینی","چرا وویس نمیفهمی","فعال شه","بازش کنم",
    ]
    if any(p in lowered for p in strong_phrases):
        return True
    payment_words=("ارتقا","پرداخت","شارژ","خرید","بخرم","موجودی")
    feature_words=("پلن","پکیج","قابلیت","عکس","وویس","ویس","صدا")
    if any(w in lowered for w in payment_words) and any(w in lowered for w in feature_words):
        return True
    if "پلن" in lowered and any(w in lowered for w in ("فعال","بخر","خرید","ارتقا","کدوم","چطور","چجوری","چطوری")):
        return True
    return False

def _admin_media_review_chat_id() -> int | None:
    settings=get_settings()
    raw=settings.admin_media_review_chat_id or settings.support_media_chat_id or ""
    return int(raw) if str(raw).lstrip("-").isdigit() else None

def _free_media_admin_caption(kind_label: str, user, sender: TelegramUser, plan: str, reason: str, caption_text: str | None) -> str:
    username=f"@{sender.username}" if sender.username else "—"
    first=sender.first_name or ""
    return f"{kind_label} ارسال‌شده از کاربر رایگان\n\nUser ID: {user.id}\nTelegram ID: {user.telegram_id}\nUsername: {username}\nName: {first}\nPlan: {plan}\nReason: {reason}\n\nمتن همراه کاربر:\n{caption_text or ''}"

async def _forward_blocked_free_media_to_admin(svc: TelegramService, msg: TelegramMessage, user, sender: TelegramUser, *, kind: str, file_id: str | None, reason: str) -> None:
    settings=get_settings()
    if not settings.admin_media_forward_enabled:
        return
    admin_chat_id=_admin_media_review_chat_id()
    if not admin_chat_id:
        if kind == "photo":
            logger.info("FREE_PHOTO_ADMIN_FORWARD_SKIPPED_NO_CHAT_ID user_id=%s", user.id)
        else:
            logger.info("FREE_VOICE_ADMIN_FORWARD_SKIPPED_NO_CHAT_ID user_id=%s", user.id)
        return
    caption=_free_media_admin_caption("📸 عکس" if kind=="photo" else "🎙️ وویس", user, sender, "free", reason, msg.caption or msg.text)
    try:
        if kind=="photo" and file_id:
            await svc.send_photo(admin_chat_id, file_id, caption)
        else:
            await svc.copy_message(admin_chat_id, msg.chat.id, msg.message_id, caption)
        if kind == "photo":
            logger.info("FREE_PHOTO_FORWARDED_TO_ADMIN user_id=%s admin_chat_id=%s", user.id, admin_chat_id)
        else:
            logger.info("FREE_VOICE_FORWARDED_TO_ADMIN user_id=%s admin_chat_id=%s", user.id, admin_chat_id)
    except Exception as exc:
        if kind == "photo":
            logger.info("FREE_PHOTO_FORWARD_FAILED user_id=%s error=%s", user.id, type(exc).__name__)
        else:
            logger.info("FREE_VOICE_FORWARD_FAILED user_id=%s error=%s", user.id, type(exc).__name__)
        with suppress(Exception):
            await svc.copy_message(admin_chat_id, msg.chat.id, msg.message_id, caption)
            if kind == "photo":
                logger.info("FREE_PHOTO_FORWARDED_TO_ADMIN user_id=%s admin_chat_id=%s", user.id, admin_chat_id)
            else:
                logger.info("FREE_VOICE_FORWARDED_TO_ADMIN user_id=%s admin_chat_id=%s", user.id, admin_chat_id)
        with suppress(Exception):
            await svc.forward_message(admin_chat_id, msg.chat.id, msg.message_id)


async def _send_user_text(svc: TelegramService, chat_id: int, text: str, *, user_id: int | None, surface: str, user_text: str | None = None, reply_markup: dict | None = None, reply_to_message_id: int | None = None, allow_sending_without_reply: bool | None = None) -> int | None:
    cleaned, issues = sanitize_user_facing_text(text, surface=surface, user_text=user_text)
    if issues:
        logger.info("OUTBOUND_TEXT_POLICY_APPLIED user_id=%s surface=%s issues=%s", user_id, surface, issues)
    if not cleaned:
        if surface == "proactive":
            logger.info("PROACTIVE_SKIPPED user_id=%s reason=outbound_policy", user_id)
        return None
    return await svc.send_text(chat_id, cleaned, reply_markup, reply_to_message_id, allow_sending_without_reply)

async def _typing_loop(svc: TelegramService, chat_id: int, user_id: int, action: str):
    logger.info("DELIVERY_TYPING_STARTED user_id=%s action=%s", user_id, action)
    try:
        while True:
            await svc.send_chat_action(chat_id, action)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        logger.info("DELIVERY_TYPING_STOPPED user_id=%s", user_id)
        raise

def _natural_delay_seconds(text: str, processing_time: float) -> float:
    base = random.uniform(0.8, 1.8) if processing_time < 1.2 else random.uniform(0.2, 1.0)
    length_bonus = min(1.7, len(text or "") / 220)
    return max(0.0, min(3.5, base + length_bonus))


async def _maybe_contextual_addon_upsell(db: Session, user, svc: TelegramService, chat_id: int, *, user_text: str, assistant_text: str) -> None:
    recent = list(reversed(db.scalars(select(Message.content).where(Message.user_id == user.id, Message.role == "user").order_by(Message.created_at.desc()).limit(4)).all()))
    suggestion = detect_addon_opportunity(db, user=user, user_text=user_text, assistant_text=assistant_text, recent_user_texts=recent)
    if not suggestion:
        return
    management_username = _management_bot_username()
    sent_message_id = await _send_user_text(
        svc,
        chat_id,
        suggestion.message_text(management_username),
        user_id=user.id,
        surface="chat",
        user_text=user_text,
        reply_markup=suggestion.keyboard(),
    )
    record_addon_upsell_event(
        db,
        user_id=user.id,
        addon_key=suggestion.addon_key,
        event_type="sent",
        reason=suggestion.reason,
        score=suggestion.score,
        message_id=None,
        metadata_json={"telegram_message_id": sent_message_id, "product_id": suggestion.product_id},
    )

async def _maybe_soft_upsell(db: Session, user, svc: TelegramService, chat_id: int) -> None:
    ok, reason = soft_upsells.eligible(db, user)
    if not ok:
        logger.info("SOFT_UPSELL_SKIPPED user_id=%s reason=%s", user.id, reason); return
    if random.random() > 0.35:
        logger.info("SOFT_UPSELL_SKIPPED user_id=%s reason=random_jitter", user.id); return
    suggestion = soft_upsells.choose_message()
    await svc.send_text(chat_id, suggestion.text, management_bot_keyboard(suggestion.cta_label, start=suggestion.management_start))
    soft_upsells.mark_sent(db, user)

async def _check_required_channel(user, svc: TelegramService) -> bool:
    settings=get_settings()
    if not settings.required_channel_enabled:
        return True
    try:
        status=await svc.get_chat_member_status(user.telegram_id, settings.required_channel_username)
        ok=status in {"creator","administrator","member"}
        logger.info("REQUIRED_CHANNEL_CHECK user_id=%s status=%s", user.id, "member" if ok else "not_member")
        if ok:
            logger.info("REQUIRED_CHANNEL_PASSED user_id=%s", user.id)
        return ok
    except Exception as exc:
        logger.warning("REQUIRED_CHANNEL_CHECK_FAILED user_id=%s reason=%s", getattr(user,"id",None), type(exc).__name__)
        logger.info("REQUIRED_CHANNEL_CHECK user_id=%s status=failed", getattr(user,"id",None))
        return False

async def _block_required_channel(user, svc: TelegramService, chat_id: int, retry: bool=False) -> None:
    logger.info("REQUIRED_CHANNEL_BLOCKED user_id=%s", user.id)
    await svc.send_message(chat_id, REQUIRED_CHANNEL_RETRY if retry else REQUIRED_CHANNEL_MESSAGE, _required_channel_keyboard())

async def _notify_admins(receipt:PaymentReceipt,user,db):
    svc=TelegramService("management"); text=f"رسید پرداخت جدید 💵\n\nکاربر: {user.display_name or '—'}\nآیدی تلگرام: @{getattr(user,'username','') or '—'}\nTelegram ID: {user.telegram_id}\nReceipt ID: {receipt.id}\nPurpose: {getattr(receipt, 'purpose', 'wallet_topup')}\nAddon: {getattr(receipt, 'addon_key', None) or '—'}{' / افزایش صمیمیت رابطه' if getattr(receipt, 'addon_key', None)==INTIMACY_MAX_UNLOCK else ''}\nPrice: {getattr(receipt, 'amount_toman', None) or getattr(receipt, 'requested_coins', None) or '—'}\n\nبرای تایید یا رد، یکی از گزینه‌ها رو بزن."
    kb={"inline_keyboard":[[{"text":"تایید پرداخت","callback_data":f"admin_payment_approve:{receipt.id}"},{"text":"رد پرداخت","callback_data":f"admin_payment_reject:{receipt.id}"}]]}
    for aid in get_settings().admin_ids:
        if receipt.telegram_file_type=="photo": await svc.send_photo(aid,receipt.telegram_file_id,text,kb)
        else: await svc.send_document(aid,receipt.telegram_file_id,text,kb)


async def _send_support_request(db:Session,user,text:str):
    svc=TelegramService("management")
    sub=orchestrator.subscriptions.get_active_subscription(db,user)
    plan=getattr(sub,"plan",None) or "free"
    body=f"📩 پیام جدید پشتیبانی\n\nکاربر: {user.display_name or '—'}\nUser ID: {user.id}\nTelegram ID: {user.telegram_id}\nپلن: {plan}\n\nمتن پیام:\n{text}\n\nبرای پاسخ، روی همین پیام ریپلای کن."
    for aid in get_settings().admin_ids:
        ticket=SupportMessage(user_id=user.id,user_telegram_id=user.telegram_id,admin_telegram_id=aid,user_message=text,status="open")
        db.add(ticket); db.flush()
        mid=await svc.send_message(aid,body)
        ticket.admin_message_id=mid
    user.admin_state=None
    logger.info("SUPPORT_REQUEST_SENT user_id=%s telegram_id=%s", user.id, user.telegram_id)

async def _handle_support_admin_reply(db:Session,msg:TelegramMessage,admin_user,svc:TelegramService) -> bool:
    if not (_is_admin(admin_user.telegram_id) and msg.reply_to_message and msg.text):
        return False
    ticket=db.scalar(select(SupportMessage).where(SupportMessage.admin_telegram_id==admin_user.telegram_id, SupportMessage.admin_message_id==msg.reply_to_message.message_id).order_by(SupportMessage.created_at.desc()))
    if not ticket:
        return False
    ticket.admin_reply=msg.text.strip(); ticket.status="replied"; ticket.replied_at=datetime.utcnow()
    await svc.send_message(ticket.user_telegram_id,f"پاسخ پشتیبانی مونس 💬\n\n{ticket.admin_reply}")
    logger.info("SUPPORT_REPLY_SENT user_id=%s ticket_id=%s admin_id=%s", ticket.user_id, ticket.id, admin_user.telegram_id)
    return True


def _audio_payload(msg: TelegramMessage):
    if msg.voice:
        return "voice", msg.voice.file_id, msg.voice.duration
    if msg.audio:
        return "audio", msg.audio.file_id, msg.audio.duration
    return None, None, None

async def _transcribe_inbound_audio(msg: TelegramMessage, user, svc: TelegramService) -> tuple[str | None, dict | None, str | None]:
    input_type, file_id, duration = _audio_payload(msg)
    if not file_id:
        return None, None, None
    logger.info("VOICE_MESSAGE_RECEIVED user_id=%s duration=%s", user.id, duration)
    destination = f"/tmp/moones_voice/{user.telegram_id}_{msg.message_id}.ogg"
    try:
        file_path = await svc.get_file_path(file_id)
        size = await svc.download_file(file_path, destination)
        logger.info("VOICE_FILE_DOWNLOADED user_id=%s bytes=%s", user.id, size)
        stt = AudioTranscriptionService()
        transcript = await stt.transcribe_telegram_voice(destination, user_id=user.id, telegram_id=str(user.telegram_id), duration=duration)
        meta = {
            "telegram_message_id": msg.message_id,
            "telegram_reply_to_message_id": getattr(msg.reply_to_message, "message_id", None),
            "input_type": input_type,
            "audio_file_id": file_id,
            "audio_duration": duration,
            "transcription_provider": stt.provider or None,
        }
        return transcript, meta, None
    except STTNotConfigured:
        return None, None, "فعلاً وویس رو نمی‌تونم گوش بدم. اگه همونو بنویسی جواب می‌دم."
    except Exception as exc:
        logger.info("VOICE_TRANSCRIPTION_FAILED user_id=%s error_type=%s", user.id, type(exc).__name__)
        return None, None, "وویست رو گرفتم، ولی نتونستم درست تبدیلش کنم. یه بار دیگه بفرست یا بنویسش."


async def _handle_inbound_photo(db: Session, msg: TelegramMessage, user, svc: TelegramService, chat_id: int, sender: TelegramUser):
    settings=get_settings()
    allowed, block_msg = media_inputs.can_use_media(db, user, "photo")
    if not allowed:
        logger.info("MEDIA_INPUT_UNAVAILABLE user_id=%s kind=photo reason=feature_disabled")
        db.commit(); await _send_user_text(svc, chat_id, block_msg or "", user_id=user.id, surface="chat"); return {"ok": True}
    photo=(msg.photo or [])[-1]
    if photo.file_size and photo.file_size > settings.max_image_bytes:
        db.commit(); await _send_user_text(svc, chat_id, "حجم عکس زیاده؛ یه نسخه سبک‌تر بفرست؟", user_id=user.id, surface="chat"); return {"ok": True}
    user_msg=Message(user_id=user.id, role="user", content="کاربر یک عکس فرستاد.", telegram_message_id=msg.message_id, telegram_reply_to_message_id=getattr(msg.reply_to_message,"message_id",None), input_type="photo")
    db.add(user_msg); db.flush()
    media=media_inputs.create_media(db,user,kind="photo",message_id=user_msg.id,telegram_message_id=msg.message_id,telegram_chat_id=chat_id,telegram_file_unique_id=photo.file_unique_id,telegram_file_id=photo.file_id,file_size=photo.file_size,width=photo.width,height=photo.height)
    logger.info("PHOTO_MESSAGE_RECEIVED user_id=%s media_ref=%s", user.id, media.media_ref)
    plan=media_inputs.plan_name(db,user)
    support_id=int(settings.support_media_chat_id) if str(settings.support_media_chat_id or "").lstrip('-').isdigit() else None
    if settings.support_media_forward_enabled and support_id:
        logger.info("PHOTO_SUPPORT_FORWARD_STARTED user_id=%s media_ref=%s", user.id, media.media_ref)
        res=await forward_photo_to_support(bot_token=svc.token,support_chat_id=support_id,source_chat_id=chat_id,source_message_id=msg.message_id,telegram_file_id=photo.file_id,media_ref=media.media_ref,user_id=user.id,telegram_user_id=user.telegram_id,username=sender.username,display_name=user.display_name,plan_name=plan,caption_text=msg.caption or msg.text)
        if res.get("ok"):
            media.support_chat_id=support_id; media.support_message_id=res.get("message_id"); media.support_forward_status="sent"; media.support_forwarded_at=res.get("forwarded_at") or datetime.utcnow(); logger.info("PHOTO_SUPPORT_FORWARD_DONE user_id=%s media_ref=%s support_message_id=%s", user.id, media.media_ref, media.support_message_id)
        else:
            media.support_forward_status="failed"; media.support_forward_error=res.get("error"); logger.info("PHOTO_SUPPORT_FORWARD_FAILED user_id=%s media_ref=%s error=%s", user.id, media.media_ref, media.support_forward_error)
    else:
        media.support_forward_status="skipped"
    await svc.send_chat_action(chat_id,"typing")
    tmp=f"/tmp/moones_media/{media.media_ref}.jpg"
    try:
        fp=await svc.get_file_path(photo.file_id); size=await svc.download_file(fp,tmp); logger.info("PHOTO_FILE_DOWNLOADED_TEMP user_id=%s media_ref=%s bytes=%s", user.id, media.media_ref, size)
        logger.info("VISION_ANALYSIS_STARTED user_id=%s media_ref=%s model=%s", user.id, media.media_ref, settings.vision_model)
        vision_charge, vision_quote = _reserve_vision_charge(
            db,
            user,
            model=settings.vision_model,
            estimated_input_tokens=VISION_ESTIMATED_INPUT_TOKENS,
            estimated_output_tokens=VISION_ESTIMATED_OUTPUT_TOKENS,
            key_suffix=str(msg.message_id),
        )
        try:
            summary=await analyze_image_with_venice(tmp,user_caption=msg.caption or msg.text,model=settings.vision_model)
            _settle_media_charge(db, vision_charge, vision_quote)
        except Exception as exc:
            _refund_media_charge(db, vision_charge, exc)
            raise
        media.summary_json=summary if settings.store_image_summary else None; media.vision_model=summary.get("model") or settings.vision_model; media.processing_status="processed"; media.processed_at=datetime.utcnow(); logger.info("VISION_ANALYSIS_DONE user_id=%s media_ref=%s confidence=%s", user.id, media.media_ref, summary.get("confidence"))
        import json
        persona_text=f"The user sent a photo.\n\nMedia reference:\n{media.media_ref}\n\nVision summary:\n{json.dumps(summary, ensure_ascii=False)}\n\nUser caption if any:\n{msg.caption or msg.text or ''}\n\nReply in Persian as the user's intimate but respectful AI companion. React like you actually noticed details in the image. Be warm, playful, and specific. Do not sound like an image captioning model. Do not say \"در تصویر می‌بینم\". Do not overdo it. If it is a selfie and safe, give a natural compliment about visible details like smile, vibe, lighting, outfit, or expression. If confidence is low, be honest and gentle. If the image may contain a minor, keep the response friendly and non-romantic."
        response=await handle_simple_chat(db,user,persona_text,message_metadata={"input_type":"photo","telegram_message_id":msg.message_id}, save_user_message=False)
        await _send_user_text(svc, chat_id, response, user_id=user.id, surface="chat", user_text=persona_text)
        media_inputs.record_media_usage(db,user,"photo"); logger.info("PHOTO_CHAT_HANDLED user_id=%s media_ref=%s", user.id, media.media_ref)
    except InsufficientCoins as exc:
        media.processing_status="failed"; media.error="insufficient_coins"
        if should_send_low_wallet_notice(db, user_id=user.id, feature="vision", dedupe_key=f"vision:{msg.message_id}"):
            await _send_user_text(svc, chat_id, feature_insufficient_text("vision", balance=exc.balance, required=exc.required), user_id=user.id, surface="chat", reply_markup=recharge_keyboard())
    except Exception as exc:
        media.processing_status="failed"; media.error=str(exc)[:1000]; logger.info("VISION_ANALYSIS_FAILED user_id=%s media_ref=%s error_type=%s error_detail=%s", user.id, media.media_ref, type(exc).__name__, str(exc)[:200]); await _send_user_text(svc, chat_id, "عکستو دریافت کردم، ولی الان نتونستم بررسیش کنم. چند دقیقه دیگه دوباره امتحان کن.", user_id=user.id, surface="chat")
    finally:
        if not settings.store_raw_user_images:
            with suppress(Exception): os.remove(tmp); logger.info("MEDIA_TEMP_FILE_DELETED user_id=%s media_ref=%s", user.id, media.media_ref)
    db.commit(); return {"ok": True}

async def _handle_inbound_voice(db: Session, msg: TelegramMessage, user, svc: TelegramService, chat_id: int):
    settings=get_settings(); kind, file_id, duration = _audio_payload(msg)
    allowed, block_msg = media_inputs.can_use_media(db, user, "voice")
    if not allowed:
        logger.info("MEDIA_INPUT_UNAVAILABLE user_id=%s kind=voice reason=feature_disabled")
        db.commit(); await _send_user_text(svc, chat_id, block_msg or "", user_id=user.id, surface="voice_reply"); return {"ok": True}
    if duration and duration > settings.max_voice_seconds:
        db.commit(); await _send_user_text(svc, chat_id, "وویس خیلی طولانیه؛ کوتاه‌تر بفرست یا متنش کن؟", user_id=user.id, surface="voice_reply"); return {"ok": True}
    payload=msg.voice if msg.voice else msg.audio
    if getattr(payload, "file_size", None) and payload.file_size > settings.max_voice_bytes:
        db.commit(); await _send_user_text(svc, chat_id, "حجم وویس زیاده؛ کوتاه‌تر بفرست یا متنش کن؟", user_id=user.id, surface="voice_reply"); return {"ok": True}
    media=media_inputs.create_media(db,user,kind=kind or "voice",telegram_message_id=msg.message_id,telegram_chat_id=chat_id,telegram_file_unique_id=getattr(payload,"file_unique_id",None),telegram_file_id=file_id,mime_type=getattr(payload,"mime_type",None),file_size=getattr(payload,"file_size",None),duration_seconds=duration)
    logger.info("VOICE_MESSAGE_RECEIVED user_id=%s media_ref=%s", user.id, media.media_ref)
    tmp=f"/tmp/moones_media/{media.media_ref}.ogg"
    try:
        fp=await svc.get_file_path(file_id); size=await svc.download_file(fp,tmp); logger.info("VOICE_FILE_DOWNLOADED_TEMP user_id=%s media_ref=%s duration=%s", user.id, media.media_ref, duration)
        stt_charge, stt_quote = _reserve_media_charge(db, user, feature="stt", model=settings.stt_model, quantity=duration or 1, key_suffix=str(msg.message_id))
        try:
            stt=await transcribe_audio_with_venice(tmp,model=settings.stt_model)
            _settle_media_charge(db, stt_charge, stt_quote)
        except Exception as exc:
            _refund_media_charge(db, stt_charge, exc)
            raise
        transcript=(stt.get("text") or "").strip(); logger.info("VOICE_TRANSCRIBED user_id=%s media_ref=%s model=%s", user.id, media.media_ref, stt.get("model"))
        user_msg=Message(user_id=user.id,role="user",content=transcript,telegram_message_id=msg.message_id,telegram_reply_to_message_id=getattr(msg.reply_to_message,"message_id",None),input_type="voice",audio_duration=duration,transcription_provider=settings.stt_provider)
        db.add(user_msg); db.flush(); media.message_id=user_msg.id; media.transcript=transcript; media.stt_model=stt.get("model"); media.processing_status="processed"; media.processed_at=datetime.utcnow()
        persona_text=f"The user sent a voice message.\n\nTranscript:\n{transcript}\n\nVoice context:\n- The user chose voice instead of text.\n- Reply naturally in Persian.\n- You may lightly acknowledge the voice, but do not claim to analyze tone unless audio analysis exists.\n- Continue the conversation based on the transcript."
        response=await handle_simple_chat(db,user,persona_text,message_metadata={"input_type":"voice","telegram_message_id":msg.message_id}, save_user_message=False)
        await _send_user_text(svc, chat_id, response, user_id=user.id, surface="chat", user_text=transcript)
        media_inputs.record_media_usage(db,user,"voice"); logger.info("VOICE_CHAT_HANDLED user_id=%s media_ref=%s", user.id, media.media_ref)
    except InsufficientCoins as exc:
        media.processing_status="failed"; media.error="insufficient_coins"
        if should_send_low_wallet_notice(db, user_id=user.id, feature="stt", dedupe_key=f"stt:{msg.message_id}"):
            await _send_user_text(svc, chat_id, feature_insufficient_text("stt", balance=exc.balance, required=exc.required), user_id=user.id, surface="voice_reply", reply_markup=recharge_keyboard())
    except Exception as exc:
        media.processing_status="failed"; media.error=str(exc)[:1000]; await _send_user_text(svc, chat_id, "وویستو گرفتم، ولی نتونستم درست بفهممش. می‌تونی دوباره بفرستی یا متنش کنی؟", user_id=user.id, surface="voice_reply")
    finally:
        if not settings.store_raw_user_images:
            with suppress(Exception): os.remove(tmp); logger.info("MEDIA_TEMP_FILE_DELETED user_id=%s media_ref=%s", user.id, media.media_ref)
    db.commit(); return {"ok": True}

async def _handle(update,db,bot_type):
    svc=TelegramService(bot_type); chat_id=None
    try:
      if update.callback_query and update.callback_query.data and update.callback_query.message:
        cb=update.callback_query; chat_id=cb.message.chat.id; sender=cb.from_user; user=onboarding.get_or_create_user(db,sender.id,sender.first_name or sender.username,sender.language_code); result=await _handle_callback(db,user,cb.data,sender.id,bot_type,svc,chat_id)
        if not isinstance(result, CallbackResult):
          text, markup = result; result = CallbackResult(text, markup)
        await svc.answer_callback_query(cb.id, result.answer_text)
        db.commit()
        if result.edit_original and result.text is not None:
          await svc.edit_message(chat_id,cb.message.message_id,result.text,result.markup)
        elif result.text:
          await svc.send_message(chat_id,result.text,result.markup)
        if bot_type=="management" and user.onboarding_complete and cb.data.startswith("onboard_"): await svc.send_message(chat_id,"منوی مونس آماده‌ست 💙",menus.main_menu())
        return {"ok":True}
      if update.message is None: return {"ok":True}
      msg=update.message; chat_id=msg.chat.id; sender=msg.from_user; user=onboarding.get_or_create_user(db,sender.id,sender.first_name or sender.username,sender.language_code)
      try:
        ensure_signup_welcome_credit(db, user=user, source=f"{bot_type}_start")
      except Exception as exc:
        db.rollback()
        logger.info("WELCOME_CREDIT_SKIPPED user_id=%s error_type=%s", user.id, type(exc).__name__)
      batch_key = forward_batches.key(bot_type, chat_id, user.id)
      if bot_type == "chat" and is_forwarded_message(msg):
        buffered, item_count, force = await forward_batches.buffer(batch_key, compact_forward_item(msg, update.update_id))
        logger.info("FORWARD_BATCH_BUFFERED user_id=%s chat_id=%s item_count=%s", user.id, chat_id, item_count)
        db.commit()
        if buffered: _schedule_forward_flush(batch_key, bot_type, update, immediate=force)
        return {"ok": True}
      if bot_type == "chat":
        async def process_pending(items): await _process_forward_items(bot_type, update, items)
        await forward_batches.flush(batch_key, process_pending)
      text=(msg.text or "").strip(); reply_context=resolve_reply_context(db,user_id=user.id,chat_id=chat_id,reply_message=msg.reply_to_message); message_metadata={"telegram_message_id": msg.message_id, "telegram_update_id": update.update_id, "telegram_reply_to_message_id": getattr(msg.reply_to_message, "message_id", None), "input_type": "text", "reply_context": reply_context}
      if bot_type == "chat" and text:
        capture_voice_feedback(db, user_id=user.id, text=text, source_message_id=msg.message_id,
                               reply_to_message_id=getattr(msg.reply_to_message, "message_id", None))
      if bot_type=="chat" and msg.photo:
        if not await _check_required_channel(user, svc):
          db.commit(); await _block_required_channel(user, svc, chat_id); return {"ok":True}
        return await _handle_inbound_photo(db, msg, user, svc, chat_id, sender)
      if bot_type=="chat" and (msg.voice or msg.audio):
        if not await _check_required_channel(user, svc):
          db.commit(); await _block_required_channel(user, svc, chat_id); return {"ok":True}
        return await _handle_inbound_voice(db, msg, user, svc, chat_id)
      if bot_type=="chat":
        settings=get_settings()
        if not await _check_required_channel(user, svc):
          db.commit(); await _block_required_channel(user, svc, chat_id); return {"ok":True}
        if not user.onboarding_complete and not settings.simple_chat_mode:
          db.commit(); await svc.send_message(chat_id,"برای شروع، اول باید پارتنر دیجیتالت رو بسازی 💙", management_bot_keyboard("شروع در ربات مدیریت")); return {"ok":True}
        if text=="/start": db.commit(); await svc.send_message(chat_id,"به مونس خوش اومدی 🌙\n\nشروعش رایگانه؛ پارتنرت رو بساز، چند دقیقه باهاش حرف بزن، بعد اگه خواستی تجربه کامل‌تر رو فعال کن."); return {"ok":True}
        if msg.sticker and not text:
          prior=db.scalar(select(Message).where(Message.user_id==user.id).order_by(Message.created_at.desc()).limit(1))
          interpretation=interpret_sticker(emoji=msg.sticker.emoji,set_name=msg.sticker.set_name,preceding_text=getattr(prior,'content',None),replying_to_sticker=bool(reply_context and reply_context.message_type=='sticker'))
          text=f"[واکنش استیکر: {interpretation.semantic_hint}; اطمینان {interpretation.confidence:.1f}. طبیعی و کوتاه واکنش نشان بده و معنی دقیق اختراع نکن.]"
          message_metadata["input_type"]="sticker"
        if not text: return {"ok":True}
        if is_upgrade_or_feature_unlock_intent(text):
          logger.info("UPGRADE_INTENT_ROUTED_TO_MANAGEMENT_BOT user_id=%s text_preview=%s", user.id, text[:80].replace("\n"," "))
          db.commit(); await _send_user_text(svc, chat_id, UPGRADE_INTENT_MESSAGE, user_id=user.id, surface="chat", user_text=text, reply_markup=_management_keyboard()); return {"ok":True}
        recent_img = db.scalar(select(__import__('app.models.image_generation', fromlist=['ImageGenerationJob']).ImageGenerationJob).where(__import__('app.models.image_generation', fromlist=['ImageGenerationJob']).ImageGenerationJob.user_id==user.id, __import__('app.models.image_generation', fromlist=['ImageGenerationJob']).ImageGenerationJob.status=='sent').order_by(__import__('app.models.image_generation', fromlist=['ImageGenerationJob']).ImageGenerationJob.sent_at.desc(), __import__('app.models.image_generation', fromlist=['ImageGenerationJob']).ImageGenerationJob.id.desc()).limit(1))
        semantic_flags = resolve_semantic_router_flags(db, user_id=user.id)
        pending_resolution = resolve_pending_image_clarification(db, user_id=user.id, text=text) if semantic_flags.execution_enabled else None
        if semantic_flags.execution_enabled and pending_resolution is None:
          supersede_pending_image_clarification(db, user_id=user.id, telegram_message_id=msg.message_id)
        deterministic_action = pending_resolution.action if pending_resolution else canonical_explicit_image_action(text)
        routing_text = pending_resolution.effective_request_text if pending_resolution and pending_resolution.action == SemanticImageAction.GENERATE_NEW and pending_resolution.effective_request_text else text
        context = build_semantic_image_router_context(db, user_id=user.id, chat_id=chat_id, current_text=routing_text, telegram_message_id=msg.message_id, reply_to_message=getattr(msg, 'reply_to_message', None), legacy_route_decision=None)
        deterministic_generate_requires_extraction = bool(deterministic_action == SemanticImageAction.GENERATE_NEW)
        if deterministic_action and not deterministic_generate_requires_extraction:
          semantic_decision = SemanticImageDecision(action=deterministic_action, media_delivery_requested=deterministic_action not in {SemanticImageAction.CHAT, SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING}, confidence=1.0, reason_code='resolved_structured_image_intent')
        else:
          semantic_decision = await SemanticImageIntentRouter(VeniceSemanticImageIntentModel()).decide(context, shadow_or_evaluation=False)
        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)
        semantic_decision = enforce_partner_photo_defaults(context, semantic_decision)
        semantic_decision = enforce_referenced_object_request(context, deterministic_action, semantic_decision)
        semantic_decision = enforce_clarification_scope(text, pending_resolution, semantic_decision)
        semantic_decision = await resolve_active_image_job_followup_semantically(context, semantic_decision)
        logger.info("IMAGE_ROUTE_LLM_DECISION user_id=%s action=%s reason_code=%s source_job_id=%s", user.id, semantic_decision.action, semantic_decision.reason_code, getattr(getattr(semantic_decision, 'source_reference', None), 'job_id', None))
        if semantic_decision.action == SemanticImageAction.STATUS_QUERY:
          target=context.active_image_job or context.latest_image_job
          text_status=_image_status_text(target) if target else None
          if text_status:
            logger.info('IMAGE_STATUS_QUERY_HANDLED user_id=%s job_id=%s request_chain_id=%s action=%s job_status=%s reason_codes=%s', user.id, target.job_id, target.request_chain_id, getattr(target, 'action', None), target.status, [])
            db.commit(); await _send_user_text(svc, chat_id, text_status, user_id=user.id, surface='chat', user_text=text); return {'ok': True}
          semantic_decision = SemanticImageDecision(action=SemanticImageAction.CHAT, media_delivery_requested=False, confidence=1.0, reason_code='status_query_without_relevant_job')
        if semantic_decision.action == SemanticImageAction.CANCEL_PENDING:
          target=context.active_image_job
          if target:
            from app.models.image_generation import ImageGenerationJob
            from app.services.image_request_state_machine import sync_image_request_chain_state, ImageRequestState
            job=db.get(ImageGenerationJob, target.job_id); job.status='cancelled'; sync_image_request_chain_state(job, ImageRequestState.CANCELLED); db.commit(); await _send_user_text(svc, chat_id, 'باشه، درخواست عکس رو لغو کردم 🤍', user_id=user.id, surface='chat', user_text=text); return {'ok': True}
          semantic_decision = SemanticImageDecision(action=SemanticImageAction.CHAT, media_delivery_requested=False, confidence=1.0, reason_code='cancel_without_active_job')
        if should_report_active_job_instead_of_enqueuing(context, semantic_decision):
          text_status=_image_status_text(context.active_image_job)
          if text_status:
            logger.info('IMAGE_ACTIVE_JOB_ABSORBED_NEW_REQUEST user_id=%s job_id=%s job_status=%s', user.id, context.active_image_job.job_id, context.active_image_job.status)
            db.commit(); await _send_user_text(svc, chat_id, text_status, user_id=user.id, surface='chat', user_text=text); return {'ok': True}
        if pending_resolution and pending_resolution.effective_request_text is None and pending_resolution.action != SemanticImageAction.CHAT:
          db.commit(); await _send_user_text(svc, chat_id, 'مهلت ابهام‌زدایی درخواست عکس قبلی تموم شده یا متن اصلیش در دسترس نیست؛ لطفاً درخواست عکس رو کامل دوباره بفرست.', user_id=user.id, surface='chat', user_text=text); return {'ok': True}
        # Pending clarification is marked resolved only after enqueue persists successfully.
        ok, source_error = validate_source_reference_deterministically(semantic_decision, recent_retrievable_image_exists=context.recent_retrievable_image_exists, allowed_job_ids={recent_img.id} if recent_img else set())
        if not ok:
          if semantic_decision.action in {SemanticImageAction.REFINE_PREVIOUS, SemanticImageAction.VARIATION, SemanticImageAction.RESEND_EXACT}:
            db.commit(); await _send_user_text(svc, chat_id, "عکس قبلیِ قابل‌دسترسی پیدا نکردم؛ اگه بخوای می‌تونم یه عکس جدید ثبت کنم.", user_id=user.id, surface="chat", user_text=text); return {"ok": True}
          semantic_decision.action = SemanticImageAction.CLARIFY; semantic_decision.needs_clarification=True; semantic_decision.media_delivery_requested=False; semantic_decision.reason_code=source_error or 'invalid_source'
        if semantic_decision.action == SemanticImageAction.CLARIFY:
          clarification = "این رو یه عکس تازه بگیرم یا همون عکس قبلی رو تغییر بدم؟"
          telegram_message_id = await _send_user_text(svc, chat_id, clarification, user_id=user.id, surface="chat", user_text=text)
          if telegram_message_id is not None:
            source_existing = next((row for row in db.scalars(select(Message).where(Message.user_id == user.id, Message.role == "assistant", Message.input_type == "image_clarification").order_by(Message.id.desc()).limit(20)).all() if (row.metadata_json or {}).get("source_user_telegram_message_id") == msg.message_id), None)
            if source_existing is None:
              source_user_row = db.scalar(select(Message).where(Message.user_id == user.id, Message.role == 'user', Message.telegram_message_id == msg.message_id).limit(1))
              if source_user_row is None:
                db.add(Message(user_id=user.id, role='user', content=text, telegram_message_id=msg.message_id, telegram_reply_to_message_id=getattr(msg.reply_to_message, 'message_id', None), input_type='text'))
                db.flush()
              db.add(Message(user_id=user.id, role="assistant", content=clarification, telegram_message_id=telegram_message_id, input_type="image_clarification", metadata_json={"source":"semantic_image_router", "kind":"pending_image_clarification", "status":"pending", "options":["generate_new", "refine_previous", "chat"], "source_user_telegram_message_id":msg.message_id}))
            db.commit()
          return {"ok": True}
        route_decision = _semantic_decision_to_legacy_route(semantic_decision, recent_img)
        logger.info("IMAGE_ROUTE_EXECUTED user_id=%s action=%s source_job_id=%s", user.id, route_decision.route, route_decision.source_image_job_id)
        if route_decision.route != 'chat':
          effective_request_text = pending_resolution.effective_request_text if pending_resolution and pending_resolution.effective_request_text else text
          resolved_request = getattr(pending_resolution, "resolved_request", None) if pending_resolution else None
          result = await _enqueue_and_acknowledge_image_request(db=db, user=user, chat_id=chat_id, message_id=msg.message_id, user_text=text, effective_request_text=effective_request_text, route_decision=route_decision, telegram_service=svc, resolved_image_request=resolved_request, pending_resolution=pending_resolution)
          logger.info("IMAGE_REQUEST_NEVER_FELL_THROUGH_TO_CHAT user_id=%s action=%s", user.id, route_decision.route)
          return result
        if settings.simple_chat_mode:
          human_presence.delivery.cancel_pending_afterthoughts(db, user, reason="user_replied")
          usage = orchestrator.subscriptions.get_or_create_today_usage(db, user)
          logger.info("TOKEN_LIMIT_ANALYTICS_ONLY user_id=%s used=%s", user.id, orchestrator.subscriptions.total_tokens_used(usage))
          recent_for_delay = list(reversed(db.scalars(select(Message).where(Message.user_id == user.id, Message.role.in_(["user", "assistant"])).order_by(Message.created_at.desc()).limit(8)).all()))
          if message_metadata.get("input_type") == "text":
            should_delay, delay_reason, delay_seconds = delayed_reactions.should_delay_user_reply(user, text, recent_for_delay)
            if should_delay and delay_seconds:
              db.add(Message(user_id=user.id, role="user", content=text, telegram_message_id=msg.message_id, telegram_reply_to_message_id=getattr(msg.reply_to_message, "message_id", None), input_type="text"))
              user.last_seen_at=datetime.utcnow(); await delayed_reactions.schedule_delayed_reply(db, user, chat_id, msg.message_id, text, delay_seconds, delay_reason or "casual_low_pressure")
              db.commit(); return {"ok": True}
          action="record_voice" if any(x in text.lower() for x in ("voice","وویس","ویس","صدا","صوتی")) else "typing"
          started=time.perf_counter(); typing_task=asyncio.create_task(_typing_loop(svc, chat_id, user.id, action))
          try:
            response=await handle_simple_chat(db,user,text,message_metadata=message_metadata)
            response_meta=getattr(response,"meta",{}) or {}
          finally:
            typing_task.cancel()
            with suppress(asyncio.CancelledError): await typing_task
          if response_meta.get("billing_status") == "insufficient_coins":
            if should_send_low_wallet_notice(db, user_id=user.id, feature="chat", dedupe_key=f"chat:{msg.message_id}"):
              await _send_user_text(svc, chat_id, str(response), user_id=user.id, surface="chat", user_text=text, reply_markup=recharge_keyboard())
            db.commit(); return {"ok": True}
          response, outbound_issues = sanitize_user_facing_text(response, surface="chat", user_text=text)
          if outbound_issues:
            logger.info("OUTBOUND_TEXT_POLICY_APPLIED user_id=%s surface=chat issues=%s", user.id, outbound_issues)
          decision=decide_delivery(user,text,response,db)
          force_text = _should_force_text_delivery(response_meta)
          voice_used=False; sticker_used=False
          if force_text:
            decision.delivery_type="text"; decision.sticker=None; decision.voice=None; decision.sticker_file_id=None
            logger.info("DELIVERY_FORCED_TEXT user_id=%s reason=style_or_confusion", user.id)
          presence_context={"delivery_type": decision.delivery_type, "response_meta": response_meta, "disable_human_extras": force_text or response_meta.get("disable_human_extras")}
          presence_plan=human_presence.build_plan(db,user,text,response,presence_context)
          if force_text or response_meta.get("disable_human_extras"):
            presence_plan.should_split=False; presence_plan.should_schedule_afterthought=False; presence_plan.should_schedule_interjection=False; presence_plan.delivery_shape="single"
            logger.info("HUMAN_EXTRA_DISABLED user_id=%s reason=style_or_confusion", user.id)
          delay=_natural_delay_seconds(response, time.perf_counter()-started); logger.info("DELIVERY_NATURAL_DELAY user_id=%s seconds=%.2f", user.id, delay); await asyncio.sleep(delay)
          if decision.delivery_type=="voice":
            if not get_settings().venice_tts_enabled:
              logger.info("VOICE_TEXT_FALLBACK user_id=%s reason=tts_disabled", user.id); decision.delivery_type="text"
              await _send_user_text(svc, chat_id, response, user_id=user.id, surface="chat", user_text=text)
            else:
             try:
              tts_model=get_settings().venice_tts_model
              voice_idem=f"tg:voice:{user.telegram_id}:{msg.message_id}:{__import__('hashlib').sha256((response or '').encode()).hexdigest()[:16]}"
              existing_voice=db.scalar(select(GeneratedVoiceOutput).where(GeneratedVoiceOutput.idempotency_key==voice_idem))
              if existing_voice and existing_voice.status=="sent" and existing_voice.user_telegram_message_id:
               voice_used=True; db.commit(); return {"ok": True}
              tts_charge, tts_quote = _reserve_media_charge(db, user, feature="tts", model=tts_model, quantity=len(response or ""), key_suffix=str(msg.message_id))
              try:
               voice_feedback_profile = load_voice_feedback_profile(db, user_id=user.id)
               selected_voice=select_tts_voice(user, {"gender": user.partner_gender, "personality_type": user.partner_personality_type}, user.current_mood, user.partner_personality_type, voice_feedback_profile)
               audio_bytes = await synthesize_voice(response, voice=selected_voice)
               _settle_media_charge(db, tts_charge, tts_quote)
              except Exception as exc:
               _refund_media_charge(db, tts_charge, exc)
               raise
              voice_name=selected_voice
              await persist_and_deliver_voice(db, user=user, chat_id=chat_id, source_telegram_message_id=msg.message_id, text=response, audio_bytes=audio_bytes, voice_name=voice_name, provider="venice", model=tts_model, usage_charge=tts_charge, telegram_service=svc)
              orchestrator.subscriptions.record_voice(db, user, response)
              voice_used=True
              if presence_plan.should_schedule_afterthought:
                human_presence.delivery.schedule_job(db,user,chat_id,"afterthought",human_presence.afterthought_text(presence_plan,response),random.randint(8,75),metadata={"source":"voice_afterthought"})
             except InsufficientCoins as exc:
              logger.info("TTS_SKIPPED_INSUFFICIENT_COINS user_id=%s required=%s balance=%s", user.id, exc.required, exc.balance)
              note = "\n\n(وویس رو فعلاً نفرستادم؛ سکه‌ات برای صدا کافی نبود 🌙)" if should_send_low_wallet_notice(db, user_id=user.id, feature="tts", dedupe_key=f"tts:{msg.message_id}") else ""
              await _send_user_text(svc, chat_id, response + note, user_id=user.id, surface="chat", user_text=text)
              decision.delivery_type="text"
             except TTSFailure as exc:
              logger.warning("TTS_RESULT success=False reason=%s user_id=%s", type(exc).__name__, user.id)
              await _send_user_text(svc, chat_id, response, user_id=user.id, surface="chat", user_text=text)
              decision.delivery_type="text"
          elif decision.delivery_type=="sticker_only" and decision.sticker_file_id:
            await svc.send_sticker(chat_id,decision.sticker_file_id); orchestrator.subscriptions.record_sticker(db,user); sticker_used=True
          else:
            parts=[response]
            if presence_plan.should_split:
              parts=human_presence.delivery.split_text(response,3)
              parts=human_presence.delivery.apply_question_guard(parts, bool(presence_plan.notes.get("rhythm",{}).get("question_density",0)>0.7), user.id)
            logger.info("HUMAN_DELIVERY_PLAN user_id=%s parts=%s afterthought=%s interjection=%s", user.id, len(parts), presence_plan.should_schedule_afterthought, presence_plan.should_schedule_interjection)
            for idx,part in enumerate(parts):
              guarded=human_presence.delivery.guard_part(db,user,part,"main" if idx==0 else "continuation")
              if not guarded: continue
              if idx>0: await asyncio.sleep(random.uniform(1.1,4.5) if idx==1 else random.uniform(1.5,6.5))
              await _send_user_text(svc, chat_id, guarded, user_id=user.id, surface="chat", user_text=text)
              logger.info("HUMAN_DELIVERY_PART_SENT user_id=%s part_index=%s total_parts=%s", user.id, idx+1, len(parts))
            if presence_plan.should_schedule_afterthought:
              human_presence.delivery.schedule_job(db,user,chat_id,"afterthought",human_presence.afterthought_text(presence_plan,response),random.randint(8,75),metadata={"source":"human_presence"})
            if presence_plan.should_schedule_interjection:
              human_presence.delivery.schedule_job(db,user,chat_id,"interjection",human_presence.interjection_text(presence_plan,text),random.randint(4,12),metadata={"source":"human_presence"})
            if decision.delivery_type=="text_plus_sticker" and decision.sticker_file_id:
              await svc.send_sticker(chat_id,decision.sticker_file_id); orchestrator.subscriptions.record_sticker(db,user); sticker_used=True
          logger.info("STICKER_RESULT selected=%s mood=%s file_id_present=%s sent=%s reason=%s", decision.delivery_type in {"text_plus_sticker","sticker_only"}, getattr(user,"current_mood",None), bool(decision.sticker_file_id), sticker_used, decision.reason)
          mark_delivery(user, decision.delivery_type, sticker_sent=sticker_used, voice_sent=voice_used)
          logger.info("SIMPLE_CHAT_FINAL user_id=%s model=%s http_status=%s raw_len=%s final_len=%s retry_used=%s delivery_type=%s voice_used=%s sticker_used=%s current_mood=%s affection_score=%s irritation_score=%s final_response_preview=%s", user.id, user.last_llm_model, user.last_llm_status_code, len(user.last_raw_llm_response or user.last_llm_response or ""), len(response), user.last_llm_retry_used, decision.delivery_type, voice_used, sticker_used, user.current_mood, user.affection_score, user.irritation_score, response[:80].replace("\n"," "))
          await _maybe_contextual_addon_upsell(db,user,svc,chat_id,user_text=text,assistant_text=response)
          await _maybe_soft_upsell(db,user,svc,chat_id)
          if message_metadata.get("input_type") in {"voice", "audio"}: logger.info("VOICE_CHAT_HANDLED user_id=%s", user.id)
          db.commit(); return {"ok":True}
        else:
          response=await orchestrator.handle_message(db,user,text)
          await _send_user_text(svc, chat_id, response, user_id=user.id, surface="chat", user_text=text)
        usage=orchestrator.subscriptions.get_or_create_today_usage(db,user); state=ensure_relationship(user.id,user.relationship_state); emotion=detect_emotion(text); ctx=stickers.context_from_message(text,response,state.stage)
        if stickers.should_send_sticker(db,ctx,state,emotion.value,usage,text):
          item=stickers.select_sticker(db,ctx,state,emotion.value,user.partner_personality_type)
          if item: usage.daily_stickers_sent += 1; db.commit(); await svc.send_sticker(chat_id,item.telegram_file_id)
        return {"ok":True}
      # management messages
      if await _handle_support_admin_reply(db,msg,user,svc): db.commit(); return {"ok":True}
      if user.admin_state=="awaiting_support_message" and text:
        await _send_support_request(db,user,text); db.commit(); await svc.send_message(chat_id,"پیامت به پشتیبانی رسید ✅\nبه‌محض بررسی، جواب همین‌جا برات ارسال می‌شه.",menus.main_menu()); return {"ok":True}
      if _is_admin(sender.id) and text=="/addsticker": user.admin_state="addsticker:awaiting_sticker"; db.commit(); await svc.send_message(chat_id,"استیکر رو بفرست تا file_id ذخیره بشه."); return {"ok":True}
      if _is_admin(sender.id) and user.admin_state=="addsticker:awaiting_sticker" and msg.sticker:
        user.admin_state=f"addsticker:metadata:{msg.sticker.file_id}:{msg.sticker.emoji or ''}:{msg.sticker.set_name or ''}"
        db.commit()
        await svc.send_message(chat_id,"استیکر دریافت شد ✅\n\nحالا مشخصاتش رو آزاد بفرست. مثال:\n\nkey=adult_wink_female\ncategory=adult_intimacy\nmeaning=شیطنت و دعوت به صمیمیت\nemojis=😏 😉 🔥\ngender=female\nmood=playful\nstages=LOVER PARTNER\nprobability=0.4\ndaily_limit=2\n\nجنسیت یعنی جنسیت پارتنر AI: female / male / neutral")
        return {"ok":True}
      if _is_admin(sender.id) and text=="/stickers":
        counts=db.execute(select(StickerItem.usage_context, func.count(StickerItem.id)).group_by(StickerItem.usage_context)).all(); last=db.scalars(select(StickerItem).order_by(StickerItem.created_at.desc()).limit(10)).all(); active=db.scalar(select(func.count(StickerItem.id)).where(StickerItem.is_active==True)) or 0; inactive=db.scalar(select(func.count(StickerItem.id)).where(StickerItem.is_active==False)) or 0
        body="استیکرها 📦\n"+"\n".join(f"{m}: {c}" for m,c in counts)+f"\nفعال: {active} | غیرفعال: {inactive}\nآخرین‌ها:\n"+"\n".join(f"#{i.id} {i.usage_context} {i.label}" for i in last)
        db.commit(); await svc.send_message(chat_id,body); return {"ok":True}
      if _is_admin(sender.id) and text.startswith("/sticker_test"):
        mood=(text.split(maxsplit=1)[1] if len(text.split(maxsplit=1))>1 else "default"); item=stickers.random_by_mood(db,mood)
        db.commit();
        if item: await svc.send_sticker(chat_id,item.telegram_file_id)
        else: await svc.send_message(chat_id,"استیکری برای این مود پیدا نشد.")
        return {"ok":True}
      if _is_admin(sender.id) and user.admin_state:
        await _handle_admin_state(db,user,text,svc,chat_id); db.commit(); return {"ok":True}
      if user.awaiting_payment_receipt and (msg.photo or msg.document):
        fid=msg.photo[-1].file_id if msg.photo else msg.document.file_id; ftype="photo" if msg.photo else "document"; purpose=getattr(user,"admin_state",None) if (user.admin_state or "").startswith("receipt_purpose:") else "wallet_topup"; addon_key=purpose.split(":",2)[2] if purpose.startswith("receipt_purpose:addon:") else None; rec=PaymentReceipt(user_id=user.id,telegram_file_id=fid,telegram_file_type=ftype,status="pending",purpose="addon" if addon_key else "wallet_topup",addon_key=addon_key); user.admin_state=None; db.add(rec); db.flush(); user.awaiting_payment_receipt=False; await _notify_admins(rec,user,db); db.commit(); await svc.send_message(chat_id,"رسیدت ثبت شد ✅\nبعد از بررسی ادمین، نتیجه همینجا بهت اطلاع داده می‌شه.",menus.main_menu()); return {"ok":True}
      if text.startswith("/start "):
        payload=text.split(" ",1)[1].strip()
        if payload=="wallet": body,markup=menus.subscription_plans(db,user),menus.subscription_keyboard()
        elif payload=="topup": body,markup=menus.topup_text(db),menus.topup_keyboard()
        elif payload=="addons": body,markup=menus.addons_text(db,user),menus.addons_keyboard(db,user)
        elif payload=="settings": body,markup=menus.settings_text(user),menus.settings_keyboard(user)
        elif payload=="adult_consent": body,markup="برای درخواست عکس بزرگسالِ داستانیِ مجاز، باید یک‌بار تأیید کنی که حداقل ۱۸ سال داری.", {"inline_keyboard":[[{"text":"تأیید می‌کنم","callback_data":"adult_content_confirm"}],[{"text":"لغو تأیید","callback_data":"adult_content_revoke"}]]}
        elif payload.startswith("addon_"):
          body,markup=menus.confirm_addon_purchase(db,user,payload[len("addon_"):].strip())
        else:
          body,markup="سلام، خوش برگشتی 💙\nاز منوی پایین هر بخش رو خواستی انتخاب کن.",menus.main_menu()
        db.commit(); await svc.send_message(chat_id,body,markup); return {"ok":True}
      if text=="/start" and user.onboarding_complete: db.commit(); await svc.send_message(chat_id,"سلام، خوش برگشتی 💙\nاز منوی پایین هر بخش رو خواستی انتخاب کن.",menus.main_menu()); return {"ok":True}
      reply=onboarding.handle_text(user,text)
      if reply or not user.onboarding_complete:
        reply=reply or onboarding.intro(); db.commit(); await svc.send_message(chat_id,reply.text,reply.reply_markup); return {"ok":True}
      mt,mm,handled=menus.handle_menu_text(db,user,text)
      if handled: db.commit(); await svc.send_message(chat_id,mt,mm or menus.main_menu()); return {"ok":True}
      db.commit(); await svc.send_message(chat_id,"برای گفتگو با پارتنرت از ربات چت مونس استفاده کن 💙",menus.chat_redirect_keyboard()); return {"ok":True}
    except Exception:
      logger.exception("Telegram webhook failed update_id=%s",update.update_id); db.rollback()
      if chat_id:
        try: await svc.send_message(chat_id,FALLBACK_ERROR_TEXT, menus.main_menu() if bot_type=="management" else None)
        except Exception: logger.exception("fallback failed")
      return {"ok":True}

def _parse_sticker_metadata(text: str, default_emoji: str | None = None) -> dict:
    raw = (text or "").strip()
    data = {
        "key": None,
        "label": None,
        "category": "normal",
        "meaning": raw or None,
        "trigger_emojis": [],
        "gender_target": "neutral",
        "mood": None,
        "usage_context": None,
        "relationship_stages": None,
        "probability": 1.0,
        "daily_limit": None,
        "weight": 1,
    }

    aliases = {
        "key": "key", "کلید": "key",
        "label": "label", "برچسب": "label", "کلمه": "label",
        "category": "category", "دسته": "category",
        "meaning": "meaning", "معنی": "meaning", "معنا": "meaning",
        "emoji": "trigger_emojis", "emojis": "trigger_emojis",
        "trigger_emojis": "trigger_emojis", "اموجی": "trigger_emojis", "ایموجی": "trigger_emojis",
        "gender": "gender_target", "gender_target": "gender_target", "جنسیت": "gender_target",
        "mood": "mood", "مود": "mood", "حال": "mood",
        "context": "usage_context", "usage_context": "usage_context",
        "stages": "relationship_stages", "stage": "relationship_stages", "relationship_stages": "relationship_stages", "مرحله": "relationship_stages",
        "probability": "probability", "prob": "probability", "احتمال": "probability",
        "daily_limit": "daily_limit", "limit": "daily_limit", "سقف": "daily_limit", "محدودیت": "daily_limit",
        "weight": "weight", "وزن": "weight",
    }

    parsed_any = False
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        sep = "=" if "=" in line else ":" if ":" in line else None
        if not sep:
            continue
        k, v = line.split(sep, 1)
        key = aliases.get(k.strip().lower())
        if not key:
            continue
        parsed_any = True
        data[key] = v.strip()

    if not parsed_any and raw:
        data["meaning"] = raw
        data["label"] = raw[:80]

    category_map = {
        "adult": "adult_intimacy", "+18": "adult_intimacy", "intimacy": "adult_intimacy",
        "adult_intimacy": "adult_intimacy", "بزرگسال": "adult_intimacy", "صمیمیت": "adult_intimacy",
        "normal": "normal", "عادی": "normal",
        "romantic": "romantic", "رمانتیک": "romantic", "عاشقانه": "romantic",
        "playful": "playful", "شیطنت": "playful", "شوخی": "playful",
    }
    cat = str(data.get("category") or "normal").strip().lower()
    data["category"] = category_map.get(cat, cat if cat in {"normal", "romantic", "playful", "adult_intimacy"} else "normal")

    gender_map = {
        "female": "female", "girl": "female", "woman": "female", "f": "female", "دختر": "female", "زن": "female",
        "male": "male", "boy": "male", "man": "male", "m": "male", "پسر": "male", "مرد": "male",
        "neutral": "neutral", "all": "neutral", "همه": "neutral", "خنثی": "neutral",
    }
    gender = str(data.get("gender_target") or "neutral").strip().lower()
    data["gender_target"] = gender_map.get(gender, "neutral")

    emoji_src = str(data.get("trigger_emojis") or "").replace(",", " ")
    emojis = [x.strip() for x in emoji_src.split() if x.strip()]
    if not emojis and default_emoji:
        emojis = [default_emoji]
    data["trigger_emojis"] = emojis

    stages_src = str(data.get("relationship_stages") or "").replace(",", " ")
    stages = [x.strip().upper() for x in stages_src.split() if x.strip()]
    data["relationship_stages"] = stages or None

    try:
        data["probability"] = max(0.0, min(1.0, float(data.get("probability") or 1)))
    except Exception:
        data["probability"] = 1.0

    try:
        raw_limit = str(data.get("daily_limit") or "").strip()
        data["daily_limit"] = int(raw_limit) if raw_limit else None
    except Exception:
        data["daily_limit"] = None

    try:
        data["weight"] = max(1, int(data.get("weight") or 1))
    except Exception:
        data["weight"] = 1

    if not data.get("label"):
        data["label"] = data.get("key") or data.get("meaning") or (emojis[0] if emojis else "sticker")
    if not data.get("key"):
        base_key = str(data["label"]).strip().replace(" ", "_")[:64]
        data["key"] = base_key or "sticker"

    return data


async def _handle_admin_state(db,user,text,svc,chat_id):
    st=user.admin_state or ""
    if st.startswith("awaiting_payment_approval_amount:"):
      rid=int(st.split(":",1)[1]); rec=db.get(PaymentReceipt,rid)
      if not rec or rec.status!="pending": user.admin_state=None; await svc.send_message(chat_id,"این رسید قبلاً بررسی شده."); return
      paid_toman,error=parse_admin_credit_amount(text)
      from app.services.coin_formatting_service import toman_to_coins, TOMAN_PER_COIN, RoundingPolicy
      coins=toman_to_coins(paid_toman, RoundingPolicy.FLOOR) if not error else None
      if error: await svc.send_message(chat_id,ADMIN_CREDIT_ERROR); return
      target=rec.user; meta=rec.metadata_json or {}
      if rec.purpose=="addon" and rec.addon_key:
       addons.activate_addon_for_user(db,user_id=target.id,addon_key=rec.addon_key,payment_receipt_id=rid,source="manual_payment",price_paid_toman=paid_toman); wallet=wallets.get_or_create_wallet(db,target); logger.info("ADDON_RECEIPT_APPROVED admin_id=%s user_id=%s addon_key=%s", user.telegram_id, target.id, rec.addon_key)
      elif meta.get("payment_type")=="subscription_renewal" and meta.get("plan"):
       orchestrator.subscriptions.renew_plan(db,target,meta["plan"]); wallet=wallets.get_or_create_wallet(db,target)
      elif meta.get("payment_type")=="plan_upgrade" and meta.get("target_plan") and meta.get("previous_expires_at"):
       orchestrator.subscriptions.apply_prorated_upgrade(db,target,meta["target_plan"],datetime.fromisoformat(meta["previous_expires_at"])); wallet=wallets.get_or_create_wallet(db,target)
      else:
       wallet=wallets.credit(db,target,coins,"manual_payment_approved",{"receipt_id":rid,"admin_id":user.telegram_id,"paid_toman":paid_toman,"toman_per_coin":TOMAN_PER_COIN,"approved_coins":coins}, idempotency_key=f"manual_receipt:{rid}:approval")
      rec.paid_toman=paid_toman; rec.amount_toman=paid_toman; rec.approved_coins=coins; rec.metadata_json={**(rec.metadata_json or {}),"paid_toman":paid_toman,"toman_per_coin":TOMAN_PER_COIN,"approved_coins":coins}; rec.status="approved"; rec.admin_id=user.telegram_id; rec.reviewed_at=datetime.utcnow(); user.admin_state=None; logger.info("PAYMENT_APPROVAL receipt_id=%s admin_id=%s user_id=%s credit=%s", rid, user.telegram_id, target.id, coins)
      await svc.send_message(chat_id,"پرداخت تایید شد ✅"); title=""
      if rec.purpose=="addon" and rec.addon_key:
       from app.models.addon import AddonProduct
       prod=db.scalar(select(AddonProduct).where(AddonProduct.key==rec.addon_key)); title=(prod.title if prod else rec.addon_key)
      await svc.send_message(target.telegram_id,(f"پرداختت تایید شد ✅ افزودنی {title} فعال شد" if rec.purpose=="addon" else f"پرداخت {paid_toman:,} تومان تأیید شد ✅\n{coins:,} سکه به کیف پولت اضافه شد.\nموجودی جدید: {wallet.balance_coins:,} سکه"))
    elif st.startswith("awaiting_payment_reject_reason:"):
      rid=int(st.split(":",1)[1]); rec=db.get(PaymentReceipt,rid)
      if rec and rec.status=="pending": rec.status="rejected"; rec.admin_id=user.telegram_id; rec.admin_note=text; rec.reviewed_at=datetime.utcnow(); logger.info("PAYMENT_REJECT receipt_id=%s admin_id=%s user_id=%s", rid, user.telegram_id, rec.user_id); await svc.send_message(rec.user.telegram_id,f"رسید پرداختت تایید نشد ❌\nدلیل: {text}\n\nاگر فکر می‌کنی اشتباهی شده، با پشتیبانی تماس بگیر.")
      user.admin_state=None; await svc.send_message(chat_id,"پرداخت رد شد.")
    elif st.startswith("addsticker:metadata:"):
      _,_,fid,emoji,setname=st.split(":",4); pack=None
      if setname:
        pack=db.scalar(select(StickerPack).where(StickerPack.telegram_set_name==setname)) or StickerPack(name=setname,telegram_set_name=setname); db.add(pack); db.flush()
      meta=_parse_sticker_metadata(text, emoji or None)
      db.add(StickerItem(
        pack_id=pack.id if pack else None,
        telegram_file_id=fid,
        emoji=(meta["trigger_emojis"][0] if meta["trigger_emojis"] else (emoji or None)),
        label=meta["label"],
        usage_context=meta["usage_context"] or meta["mood"] or meta["category"] or "comfort",
        relationship_stage_min=(meta["relationship_stages"][0] if meta["relationship_stages"] else "STRANGER"),
        weight=meta["weight"],
        is_active=True,
        key=meta["key"],
        category=meta["category"],
        meaning=meta["meaning"],
        trigger_emojis=meta["trigger_emojis"] or None,
        mood=meta["mood"],
        gender_target=meta["gender_target"],
        relationship_stages=meta["relationship_stages"],
        enabled=True,
        probability=meta["probability"],
        daily_limit=meta["daily_limit"],
      ))
      user.admin_state=None
      await svc.send_message(chat_id,f"استیکر ذخیره شد ✅\ncategory: {meta['category']}\ngender: {meta['gender_target']}\nmeaning: {meta['meaning'] or '—'}\nemojis: {' '.join(meta['trigger_emojis']) or '—'}")
    elif st.startswith("addsticker:label:"):
      _,_,fid,emoji,setname=st.split(":",4); pack=None
      if setname:
        pack=db.scalar(select(StickerPack).where(StickerPack.telegram_set_name==setname)) or StickerPack(name=setname,telegram_set_name=setname); db.add(pack); db.flush()
      db.add(StickerItem(pack_id=pack.id if pack else None,telegram_file_id=fid,emoji=emoji or None,label=text,usage_context="comfort",relationship_stage_min="STRANGER",key=text[:64],category="normal",meaning=text,trigger_emojis=[emoji] if emoji else None,gender_target="neutral",enabled=True,probability=1)); user.admin_state=None; await svc.send_message(chat_id,"استیکر ذخیره شد ✅")

async def _handle_callback(db,user,data,telegram_id,bot_type,svc=None,chat_id=None):
 if data.startswith("imgfb:"):
  _, job_id, rating = data.split(":", 2)
  store_feedback(db, user_id=user.id, job_id=int(job_id), rating=rating)
  return ("مرسی، نظرت ذخیره شد 🤍", None)
 if data.startswith("voicefb:"):
  _, voice_id, rating = data.split(":", 2)
  if not store_voice_feedback(db, user_id=user.id, voice_id=int(voice_id), rating=rating):
   return ("این بازخورد برای این کاربر معتبر نیست.", None)
  return ("مرسی، نظرت درباره وویس ذخیره شد 🤍", None)
 if data == "adult_content_confirm" or data == "adult_content_revoke":
  return ("این گزینه قدیمی شده. برای مدیریت تصاویر بزرگسال از افزودنی «تصاویر بزرگسال مونس» استفاده کن.", management_bot_keyboard("مدیریت افزودنی", start="addon_adult_image_generation_unlock"))
 if bot_type=="chat":
  if data=="check_required_channel" and svc and chat_id:
   if await _check_required_channel(user, svc): return CallbackResult("عضویتت تأیید شد ✅\nحالا می‌تونی از مونس استفاده کنی 🌙",None)
   await _block_required_channel(user, svc, chat_id, retry=True); return CallbackResult(REQUIRED_CHANNEL_RETRY,_required_channel_keyboard())
  stale_map={"sub_back":"wallet","sub_status":"wallet","wallet_status":"wallet","sub_go_topup":"topup","wallet_topup_menu":"topup","addons_menu":"addons","proactive_toggle":"settings","proactive_on":"settings","proactive_off":"settings","adult_content_confirm":"adult_consent"}
  start=stale_map.get(data) or (data.split(":",1)[1] if data.startswith("addon_buy:") else None)
  if start and not start.startswith("addon_") and data.startswith("addon_buy:"): start=f"addon_{start}"
  if not start and data.startswith("addon_confirm:"): start=f"addon_{data.split(':',1)[1]}"
  logger.warning("STALE_MANAGEMENT_CALLBACK_IN_CHAT user_id=%s callback=%s", user.id, data)
  return CallbackResult("این گزینه از ربات مدیریت باز می‌شه 🌙", management_bot_keyboard("باز کردن ربات مدیریت", start=start or "wallet"), edit_original=False, answer_text="از دکمه ربات مدیریت استفاده کن 🌙")
 if data=="about_moones": return menus.about_text(), None
 if data.startswith("onboard_") or data.startswith("onboarding:"):
  was_complete = user.onboarding_complete
  r=onboarding.handle_callback(user,data)
  if user.onboarding_complete:
   wallets.get_or_create_wallet(db,user); onboarding.subscriptions.ensure_free_subscription(db,user)
   if not was_complete:
    grant_result = ensure_signup_welcome_credit(db, user=user, source="onboarding_complete")
    if grant_result.status == "granted" and user.welcome_coins_amount:
     r.text = f"{r.text}\n\n{_persian_digits(user.welcome_coins_amount)} سکه هدیه شروع هم به کیف پولت اضافه شد 🎁"
  return r.text,r.reply_markup
 if data in {"go_chat"}: return menus.chat_redirect_text(),menus.chat_redirect_keyboard()
 if data.startswith("sub_activate_"): return menus.activate_subscription(db,user,data.rsplit("_",1)[1])
 if data in {"sub_status","wallet_status"}: return menus.subscription_status_text(db,user),menus.subscription_keyboard()
 if data=="addons_menu": return menus.addons_text(db,user),menus.addons_keyboard(db,user)
 if data=="addon_buy_intimacy_max": return menus.confirm_addon_purchase(db,user,INTIMACY_MAX_UNLOCK)
 if data=="addon_confirm_intimacy_max": return menus.activate_addon_from_wallet(db,user,INTIMACY_MAX_UNLOCK)
 if data.startswith("addon_buy:"):
  key=data.split(":",1)[1]
  return menus.confirm_addon_purchase(db,user,key)
 if data.startswith("addon_confirm:"):
  key=data.split(":",1)[1]
  return menus.activate_addon_from_wallet(db,user,key)
 if data.startswith("addon_toggle:"):
  _, key, state = data.split(":",2)
  return menus.toggle_addon(db,user,key,state=="on")
 if data in {"sub_go_topup","wallet_topup_menu"}: return menus.topup_text(db),menus.topup_keyboard()
 if data=="sub_back": return menus.subscription_plans(db,user),menus.subscription_keyboard()
 if data=="payment_i_paid": user.awaiting_payment_receipt=True; return "لطفاً اسکرین‌شات رسید پرداخت رو همینجا ارسال کن 🙏",None
 if data=="wallet_history": return menus.history_text(db,user),None
 if data=="wallet_receipts": return menus.receipts_text(db,user),None
 if data=="partner_edit_prompt": return "برای ویرایش پارتنر، باید دوباره فرایند ساخت رو انجام بدی.\nادامه می‌دی؟",menus.partner_edit_prompt_keyboard()
 if data=="partner_edit_confirm": r=onboarding.reset_for_edit(user); return r.text,r.reply_markup
 if data=="partner_edit_cancel": return "باشه، پارتنرت بدون تغییر می‌مونه 💙",None
 if data in {"proactive_toggle","proactive_on","proactive_off"}:
  enabled = getattr(user,"proactive_messages_enabled",True) is not False
  new_state = (not enabled) if data=="proactive_toggle" else (data=="proactive_on")
  user.proactive_messages_enabled=new_state
  if new_state:
   ProactiveService().schedule_next_proactive(db,user,reason="user_enabled"); logger.info("PROACTIVE_USER_ENABLED user_id=%s", user.id)
  else:
   user.next_proactive_at=None; logger.info("PROACTIVE_USER_DISABLED user_id=%s", user.id)
  return menus.settings_text(user), menus.settings_keyboard(user)
 if data.startswith("admin_payment_approve:") and _is_admin(telegram_id):
  rid=data.split(":")[1]; rec=db.get(PaymentReceipt,int(rid))
  if not rec or rec.status!="pending": return "این رسید قبلاً بررسی شده.",None
  user.admin_state=f"awaiting_payment_approval_amount:{rid}"; return f"تایید رسید #{rid}\nکاربر: {rec.user.display_name or rec.user.telegram_id}\nمبلغ تومان را وارد کن:",None
 if data.startswith("admin_payment_reject:") and _is_admin(telegram_id):
  rid=data.split(":")[1]; rec=db.get(PaymentReceipt,int(rid))
  if not rec or rec.status!="pending": return "این رسید قبلاً بررسی شده.",None
  user.admin_state=f"awaiting_payment_reject_reason:{rid}"; return f"رد رسید #{rid}\nکاربر: {rec.user.display_name or rec.user.telegram_id}\nدلیل رد را بنویس:",None
 if data.startswith("addsticker_mood:") and _is_admin(telegram_id) and (user.admin_state or "").startswith("addsticker:mood:"):
  mood=data.split(":",1)[1]; _,_,fid,emoji,setname=(user.admin_state or "").split(":",4); pack=None
  if setname:
   pack=db.scalar(select(StickerPack).where(StickerPack.telegram_set_name==setname)) or StickerPack(name=setname,telegram_set_name=setname); db.add(pack); db.flush()
  item=StickerItem(pack_id=pack.id if pack else None, telegram_file_id=fid, emoji=emoji or None, label=emoji or mood, usage_context=mood, weight=1, is_active=True); db.add(item); db.flush(); user.admin_state=None
  return f"استیکر ذخیره شد ✅\nfile_id: {fid}\nemoji: {emoji or '—'}\nset: {setname or '—'}\nmood: {mood}",None
 return "این گزینه معتبر نیست؛ لطفاً دوباره از منو انتخاب کن 💙",None
