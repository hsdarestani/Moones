from __future__ import annotations
import asyncio
import logging
import random
import time
from contextlib import suppress
from datetime import datetime
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.db.session import get_db
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
from app.services.wallet_service import WalletService
from app.services.sticker_service import StickerService
from app.services.subscription_service import LIMIT_MESSAGE
from app.services.credit_validation import ADMIN_CREDIT_ERROR, parse_admin_credit_amount
from app.services.soft_upsell_service import SoftUpsellService

logger=logging.getLogger(__name__); router=APIRouter(prefix="/telegram", tags=["telegram"])
orchestrator=ConversationOrchestrator(); onboarding=OnboardingService(); menus=BotMenuService(); wallets=WalletService(); stickers=StickerService(); soft_upsells=SoftUpsellService()
FALLBACK_ERROR_TEXT="یه مشکلی پیش اومد 😅\nدوباره امتحان کن، من اینجام."
LIMITED_MEDIA_MESSAGE="فعلاً با متن کنارت می‌مونم 🌙"
FAIR_USE_MESSAGE="برای حفظ کیفیت تجربه، امروز یه کم آروم‌تر ادامه می‌دم. هنوز اینجام، فقط فعلاً بیشتر با متن جواب می‌دم 🌙"
REQUIRED_CHANNEL_MESSAGE="برای استفاده از مونس، اول عضو کانال آپدیت‌ها شو 🌙\n\nاونجا خبر قابلیت‌های جدید، آپدیت‌ها و هدیه‌ها رو می‌ذاریم."
REQUIRED_CHANNEL_RETRY="هنوز عضویتت تأیید نشده. اول عضو کانال شو، بعد دوباره بزن عضو شدم ✅"
class TelegramUser(BaseModel): id:int; first_name:str|None=None; username:str|None=None; language_code:str|None=None
class TelegramChat(BaseModel): id:int
class TelegramPhoto(BaseModel): file_id:str; file_unique_id:str|None=None; file_size:int|None=None
class TelegramDocument(BaseModel): file_id:str; file_name:str|None=None; mime_type:str|None=None
class TelegramSticker(BaseModel): file_id:str; emoji:str|None=None; set_name:str|None=None
class TelegramMessage(BaseModel):
    message_id:int; from_user:TelegramUser=Field(alias="from"); chat:TelegramChat; text:str|None=None; photo:list[TelegramPhoto]|None=None; document:TelegramDocument|None=None; sticker:TelegramSticker|None=None; reply_to_message:TelegramMessage|None=None
class TelegramCallbackQuery(BaseModel): id:str; from_user:TelegramUser=Field(alias="from"); message:TelegramMessage|None=None; data:str|None=None
class TelegramUpdate(BaseModel): update_id:int; message:TelegramMessage|None=None; callback_query:TelegramCallbackQuery|None=None

@router.post("/webhook")
@router.post("/management/webhook")
async def management_webhook(update:TelegramUpdate, request:Request, db:Session=Depends(get_db)): return await _handle(update,db,"management")
@router.post("/chat/webhook")
async def chat_webhook(update:TelegramUpdate, request:Request, db:Session=Depends(get_db)): return await _handle(update,db,"chat")

def _is_admin(tid:int)->bool: return tid in get_settings().admin_ids

def _required_channel_keyboard():
    settings=get_settings()
    return {"inline_keyboard":[[{"text":"عضویت در کانال MoonesAI","url":settings.required_channel_url}],[{"text":"عضو شدم ✅","callback_data":"check_required_channel"}]]}


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

async def _maybe_soft_upsell(db: Session, user, svc: TelegramService, chat_id: int) -> None:
    ok, reason = soft_upsells.eligible(db, user)
    if not ok:
        logger.info("SOFT_UPSELL_SKIPPED user_id=%s reason=%s", user.id, reason); return
    if random.random() > 0.35:
        logger.info("SOFT_UPSELL_SKIPPED user_id=%s reason=random_jitter", user.id); return
    await svc.send_text(chat_id, soft_upsells.choose_message(), soft_upsells.keyboard())
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
    svc=TelegramService("management"); text=f"رسید پرداخت جدید 💵\n\nکاربر: {user.display_name or '—'}\nآیدی تلگرام: @{getattr(user,'username','') or '—'}\nTelegram ID: {user.telegram_id}\nReceipt ID: {receipt.id}\n\nبرای تایید یا رد، یکی از گزینه‌ها رو بزن."
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
    from sqlalchemy import select
    ticket=db.scalar(select(SupportMessage).where(SupportMessage.admin_telegram_id==admin_user.telegram_id, SupportMessage.admin_message_id==msg.reply_to_message.message_id).order_by(SupportMessage.created_at.desc()))
    if not ticket:
        return False
    ticket.admin_reply=msg.text.strip(); ticket.status="replied"; ticket.replied_at=datetime.utcnow()
    await svc.send_message(ticket.user_telegram_id,f"پاسخ پشتیبانی مونس 💬\n\n{ticket.admin_reply}")
    logger.info("SUPPORT_REPLY_SENT user_id=%s ticket_id=%s admin_id=%s", ticket.user_id, ticket.id, admin_user.telegram_id)
    return True

async def _handle(update,db,bot_type):
    svc=TelegramService(bot_type); chat_id=None
    try:
      if update.callback_query and update.callback_query.data and update.callback_query.message:
        cb=update.callback_query; chat_id=cb.message.chat.id; sender=cb.from_user; user=onboarding.get_or_create_user(db,sender.id,sender.first_name or sender.username,sender.language_code); await svc.answer_callback_query(cb.id)
        text,markup=await _handle_callback(db,user,cb.data,sender.id,bot_type,svc,chat_id); db.commit(); await svc.edit_message(chat_id,cb.message.message_id,text,markup)
        if bot_type=="management" and user.onboarding_complete and cb.data.startswith("onboard_"): await svc.send_message(chat_id,"منوی مونس آماده‌ست 💙",menus.main_menu())
        return {"ok":True}
      if update.message is None: return {"ok":True}
      msg=update.message; chat_id=msg.chat.id; sender=msg.from_user; user=onboarding.get_or_create_user(db,sender.id,sender.first_name or sender.username,sender.language_code); text=(msg.text or "").strip()
      if bot_type=="chat":
        settings=get_settings()
        if not await _check_required_channel(user, svc):
          db.commit(); await _block_required_channel(user, svc, chat_id); return {"ok":True}
        if not user.onboarding_complete and not settings.simple_chat_mode:
          u=(settings.telegram_management_bot_username or "MonesBot"); u=u if u.startswith('@') else '@'+u
          db.commit(); await svc.send_message(chat_id,f"برای شروع، اول باید پارتنر دیجیتالت رو بسازی 💙\nاز ربات مدیریت مونس شروع کن:\n\n{u}"); return {"ok":True}
        if text=="/start": db.commit(); await svc.send_message(chat_id,"به مونس خوش اومدی 🌙\n\nشروعش رایگانه؛ پارتنرت رو بساز، چند دقیقه باهاش حرف بزن، بعد اگه خواستی تجربه کامل‌تر رو فعال کن."); return {"ok":True}
        if not text: return {"ok":True}
        if settings.simple_chat_mode:
          allowed, token_limit, usage = orchestrator.subscriptions.can_generate(db, user)
          if not allowed:
            logger.info("TOKEN_LIMIT_BLOCKED user_id=%s used=%s limit=%s", user.id, orchestrator.subscriptions.total_tokens_used(usage), token_limit)
            db.commit(); await svc.send_text(chat_id, LIMIT_MESSAGE); return {"ok":True}
          action="record_voice" if any(x in text.lower() for x in ("voice","وویس","ویس","صدا","صوتی")) else "typing"
          started=time.perf_counter(); typing_task=asyncio.create_task(_typing_loop(svc, chat_id, user.id, action))
          try:
            response=await handle_simple_chat(db,user,text)
          finally:
            typing_task.cancel()
            with suppress(asyncio.CancelledError): await typing_task
          response=sanitize_final_response(response,text)
          decision=decide_delivery(user,text,response,db)
          voice_used=False; sticker_used=False
          delay=_natural_delay_seconds(response, time.perf_counter()-started); logger.info("DELIVERY_NATURAL_DELAY user_id=%s seconds=%.2f", user.id, delay); await asyncio.sleep(delay)
          if decision.delivery_type=="voice":
            can_voice, voice_limit, usage = orchestrator.subscriptions.can_send_voice(db, user)
            if not can_voice:
              logger.info("DELIVERY_DECISION type=text reason=voice_quota_exhausted user_id=%s limit=%s", user.id, voice_limit)
              logger.info("VOICE_UNAVAILABLE_SILENT_TEXT_FALLBACK user_id=%s reason=quota", user.id); decision.delivery_type="text"
            else:
             try:
              await svc.send_voice(chat_id, await synthesize_voice(response, voice=select_tts_voice(user, {"gender": user.partner_gender, "personality_type": user.partner_personality_type}, user.current_mood, user.partner_personality_type)), None)
              orchestrator.subscriptions.record_voice(db, user, response)
              voice_used=True
             except TTSFailure as exc:
              logger.warning("TTS_RESULT success=False reason=%s user_id=%s", type(exc).__name__, user.id)
              await svc.send_text(chat_id,response)
              decision.delivery_type="text"
          elif decision.delivery_type=="sticker_only" and decision.sticker_file_id:
            can_sticker, _, _ = orchestrator.subscriptions.can_send_sticker(db, user)
            if can_sticker:
              await svc.send_sticker(chat_id,decision.sticker_file_id); orchestrator.subscriptions.record_sticker(db,user); sticker_used=True
            else:
              logger.info("STICKER_UNAVAILABLE_SILENT_FALLBACK user_id=%s reason=quota", user.id)
          else:
            await svc.send_text(chat_id,response)
            if decision.delivery_type=="text_plus_sticker" and decision.sticker_file_id:
              can_sticker, _, _ = orchestrator.subscriptions.can_send_sticker(db, user)
              if can_sticker:
                await svc.send_sticker(chat_id,decision.sticker_file_id); orchestrator.subscriptions.record_sticker(db,user); sticker_used=True
              else:
                logger.info("STICKER_UNAVAILABLE_SILENT_FALLBACK user_id=%s reason=quota", user.id)
          logger.info("STICKER_RESULT selected=%s mood=%s file_id_present=%s sent=%s reason=%s", decision.delivery_type in {"text_plus_sticker","sticker_only"}, getattr(user,"current_mood",None), bool(decision.sticker_file_id), sticker_used, decision.reason)
          mark_delivery(user, decision.delivery_type, sticker_sent=sticker_used, voice_sent=voice_used)
          logger.info("SIMPLE_CHAT_FINAL user_id=%s model=%s http_status=%s raw_len=%s final_len=%s retry_used=%s delivery_type=%s voice_used=%s sticker_used=%s current_mood=%s affection_score=%s irritation_score=%s final_response_preview=%s", user.id, user.last_llm_model, user.last_llm_status_code, len(user.last_raw_llm_response or user.last_llm_response or ""), len(response), user.last_llm_retry_used, decision.delivery_type, voice_used, sticker_used, user.current_mood, user.affection_score, user.irritation_score, response[:80].replace("\n"," "))
          await _maybe_soft_upsell(db,user,svc,chat_id)
          db.commit(); return {"ok":True}
        else:
          response=await orchestrator.handle_message(db,user,text)
        await svc.send_message(chat_id,response)
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
        user.admin_state=f"addsticker:mood:{msg.sticker.file_id}:{msg.sticker.emoji or ''}:{msg.sticker.set_name or ''}"; db.commit(); await svc.send_message(chat_id,"کاربرد استیکر رو انتخاب کن:", {"inline_keyboard":[[{"text":m,"callback_data":f"addsticker_mood:{m}"}] for m in ["warm","upset","sad","playful","love","comfort","neutral"]]}); return {"ok":True}
      if _is_admin(sender.id) and text=="/stickers":
        from sqlalchemy import func, select
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
        fid=msg.photo[-1].file_id if msg.photo else msg.document.file_id; ftype="photo" if msg.photo else "document"; rec=PaymentReceipt(user_id=user.id,telegram_file_id=fid,telegram_file_type=ftype,status="pending"); db.add(rec); db.flush(); user.awaiting_payment_receipt=False; await _notify_admins(rec,user,db); db.commit(); await svc.send_message(chat_id,"رسیدت ثبت شد ✅\nبعد از بررسی ادمین، نتیجه همینجا بهت اطلاع داده می‌شه.",menus.main_menu()); return {"ok":True}
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

async def _handle_admin_state(db,user,text,svc,chat_id):
    st=user.admin_state or ""
    if st.startswith("awaiting_payment_approval_amount:"):
      rid=int(st.split(":",1)[1]); rec=db.get(PaymentReceipt,rid)
      if not rec or rec.status!="pending": user.admin_state=None; await svc.send_message(chat_id,"این رسید قبلاً بررسی شده."); return
      coins,error=parse_admin_credit_amount(text)
      if error: await svc.send_message(chat_id,ADMIN_CREDIT_ERROR); return
      target=rec.user; meta=rec.metadata_json or {}
      if meta.get("payment_type")=="plan_upgrade" and meta.get("target_plan") and meta.get("previous_expires_at"):
       orchestrator.subscriptions.apply_prorated_upgrade(db,target,meta["target_plan"],datetime.fromisoformat(meta["previous_expires_at"])); wallet=wallets.get_or_create_wallet(db,target)
      else:
       wallet=wallets.credit(db,target,coins,"manual_payment_approved",{"receipt_id":rid,"admin_id":user.telegram_id})
      rec.status="approved"; rec.admin_id=user.telegram_id; rec.reviewed_at=datetime.utcnow(); user.admin_state=None; logger.info("PAYMENT_APPROVAL receipt_id=%s admin_id=%s user_id=%s credit=%s", rid, user.telegram_id, target.id, coins)
      await svc.send_message(chat_id,f"پرداخت تایید شد ✅\n{coins:,} تومان به اعتبار کاربر اضافه شد."); await svc.send_message(target.telegram_id,f"پرداختت تایید شد ✅\n{coins:,} تومان به اعتبارت اضافه شد.\n\nاعتبار فعلی: {wallet.balance_coins:,} تومان")
    elif st.startswith("awaiting_payment_reject_reason:"):
      rid=int(st.split(":",1)[1]); rec=db.get(PaymentReceipt,rid)
      if rec and rec.status=="pending": rec.status="rejected"; rec.admin_id=user.telegram_id; rec.admin_note=text; rec.reviewed_at=datetime.utcnow(); logger.info("PAYMENT_REJECT receipt_id=%s admin_id=%s user_id=%s", rid, user.telegram_id, rec.user_id); await svc.send_message(rec.user.telegram_id,f"رسید پرداختت تایید نشد ❌\nدلیل: {text}\n\nاگر فکر می‌کنی اشتباهی شده، با پشتیبانی تماس بگیر.")
      user.admin_state=None; await svc.send_message(chat_id,"پرداخت رد شد.")
    elif st.startswith("addsticker:label:"):
      _,_,fid,emoji,setname=st.split(":",4); pack=None
      if setname:
        from sqlalchemy import select
        pack=db.scalar(select(StickerPack).where(StickerPack.telegram_set_name==setname)) or StickerPack(name=setname,telegram_set_name=setname); db.add(pack); db.flush()
      db.add(StickerItem(pack_id=pack.id if pack else None,telegram_file_id=fid,emoji=emoji or None,label=text,usage_context="comfort",relationship_stage_min="STRANGER")); user.admin_state=None; await svc.send_message(chat_id,"استیکر ذخیره شد ✅ (context پیش‌فرض: comfort)")

async def _handle_callback(db,user,data,telegram_id,bot_type,svc=None,chat_id=None):
 if bot_type=="chat":
  if data=="check_required_channel" and svc and chat_id:
   if await _check_required_channel(user, svc): return "عضویتت تأیید شد ✅\nحالا می‌تونی از مونس استفاده کنی 🌙",None
   await _block_required_channel(user, svc, chat_id, retry=True); return REQUIRED_CHANNEL_RETRY,_required_channel_keyboard()
  return "این ربات فقط برای چته 💙",None
 if data=="about_moones": return menus.about_text(), None
 if data.startswith("onboard_") or data.startswith("onboarding:"):
  r=onboarding.handle_callback(user,data)
  if user.onboarding_complete: wallets.get_or_create_wallet(db,user); onboarding.subscriptions.ensure_free_subscription(db,user)
  return r.text,r.reply_markup
 if data in {"go_chat"}: return menus.chat_redirect_text(),menus.chat_redirect_keyboard()
 if data.startswith("sub_activate_"): return menus.activate_subscription(db,user,data.rsplit("_",1)[1])
 if data=="sub_status": return menus.subscription_status_text(db,user),None
 if data in {"sub_go_topup","wallet_topup_menu"}: return menus.topup_text(db),menus.topup_keyboard()
 if data=="sub_back": return menus.subscription_plans(db,user),menus.subscription_keyboard()
 if data=="payment_i_paid": user.awaiting_payment_receipt=True; return "لطفاً اسکرین‌شات رسید پرداخت رو همینجا ارسال کن 🙏",None
 if data=="wallet_history": return menus.history_text(db,user),None
 if data=="wallet_receipts": return menus.receipts_text(db,user),None
 if data=="partner_edit_prompt": return "برای ویرایش پارتنر، باید دوباره فرایند ساخت رو انجام بدی.\nادامه می‌دی؟",menus.partner_edit_prompt_keyboard()
 if data=="partner_edit_confirm": r=onboarding.reset_for_edit(user); return r.text,r.reply_markup
 if data=="partner_edit_cancel": return "باشه، پارتنرت بدون تغییر می‌مونه 💙",None
 if data=="proactive_on": user.proactive_messages_enabled=True; return "پیام‌های خودجوش مونس روشن شد 💙\nگاهی خودش هم سراغت میاد.", menus.settings_keyboard()
 if data=="proactive_off": user.proactive_messages_enabled=False; return "پیام‌های خودجوش مونس خاموش شد. هر وقت خواستی دوباره روشنش کن 💙", menus.settings_keyboard()
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
   from sqlalchemy import select
   pack=db.scalar(select(StickerPack).where(StickerPack.telegram_set_name==setname)) or StickerPack(name=setname,telegram_set_name=setname); db.add(pack); db.flush()
  item=StickerItem(pack_id=pack.id if pack else None, telegram_file_id=fid, emoji=emoji or None, label=emoji or mood, usage_context=mood, weight=1, is_active=True); db.add(item); db.flush(); user.admin_state=None
  return f"استیکر ذخیره شد ✅\nfile_id: {fid}\nemoji: {emoji or '—'}\nset: {setname or '—'}\nmood: {mood}",None
 return "این گزینه معتبر نیست؛ لطفاً دوباره از منو انتخاب کن 💙",None
