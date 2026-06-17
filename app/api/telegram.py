import logging
from datetime import datetime
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.db.session import get_db
from app.engine.orchestrator import ConversationOrchestrator
from app.engine.simple_chat import handle_simple_chat
from app.engine.emotion_engine import detect_emotion
from app.engine.relationship_engine import ensure_relationship
from app.models.payment import PaymentReceipt
from app.models.sticker import StickerItem, StickerPack
from app.services.bot_menu_service import BotMenuService
from app.services.onboarding_service import OnboardingService
from app.services.telegram_service import TelegramService
from app.services.wallet_service import WalletService
from app.services.sticker_service import StickerService

logger=logging.getLogger(__name__); router=APIRouter(prefix="/telegram", tags=["telegram"])
orchestrator=ConversationOrchestrator(); onboarding=OnboardingService(); menus=BotMenuService(); wallets=WalletService(); stickers=StickerService()
FALLBACK_ERROR_TEXT="یه مشکلی پیش اومد 😅\nدوباره امتحان کن، من اینجام."
class TelegramUser(BaseModel): id:int; first_name:str|None=None; username:str|None=None; language_code:str|None=None
class TelegramChat(BaseModel): id:int
class TelegramPhoto(BaseModel): file_id:str; file_unique_id:str|None=None; file_size:int|None=None
class TelegramDocument(BaseModel): file_id:str; file_name:str|None=None; mime_type:str|None=None
class TelegramSticker(BaseModel): file_id:str; emoji:str|None=None; set_name:str|None=None
class TelegramMessage(BaseModel):
    message_id:int; from_user:TelegramUser=Field(alias="from"); chat:TelegramChat; text:str|None=None; photo:list[TelegramPhoto]|None=None; document:TelegramDocument|None=None; sticker:TelegramSticker|None=None
class TelegramCallbackQuery(BaseModel): id:str; from_user:TelegramUser=Field(alias="from"); message:TelegramMessage|None=None; data:str|None=None
class TelegramUpdate(BaseModel): update_id:int; message:TelegramMessage|None=None; callback_query:TelegramCallbackQuery|None=None

@router.post("/webhook")
@router.post("/management/webhook")
async def management_webhook(update:TelegramUpdate, request:Request, db:Session=Depends(get_db)): return await _handle(update,db,"management")
@router.post("/chat/webhook")
async def chat_webhook(update:TelegramUpdate, request:Request, db:Session=Depends(get_db)): return await _handle(update,db,"chat")

def _is_admin(tid:int)->bool: return tid in get_settings().admin_ids
async def _notify_admins(receipt:PaymentReceipt,user,db):
    svc=TelegramService("management"); text=f"رسید پرداخت جدید 💵\n\nکاربر: {user.display_name or '—'}\nآیدی تلگرام: @{getattr(user,'username','') or '—'}\nTelegram ID: {user.telegram_id}\nReceipt ID: {receipt.id}\n\nبرای تایید یا رد، یکی از گزینه‌ها رو بزن."
    kb={"inline_keyboard":[[{"text":"تایید پرداخت","callback_data":f"admin_payment_approve:{receipt.id}"},{"text":"رد پرداخت","callback_data":f"admin_payment_reject:{receipt.id}"}]]}
    for aid in get_settings().admin_ids:
        if receipt.telegram_file_type=="photo": await svc.send_photo(aid,receipt.telegram_file_id,text,kb)
        else: await svc.send_document(aid,receipt.telegram_file_id,text,kb)

async def _handle(update,db,bot_type):
    svc=TelegramService(bot_type); chat_id=None
    try:
      if update.callback_query and update.callback_query.data and update.callback_query.message:
        cb=update.callback_query; chat_id=cb.message.chat.id; sender=cb.from_user; user=onboarding.get_or_create_user(db,sender.id,sender.first_name or sender.username,sender.language_code); await svc.answer_callback_query(cb.id)
        text,markup=await _handle_callback(db,user,cb.data,sender.id,bot_type); db.commit(); await svc.edit_message(chat_id,cb.message.message_id,text,markup)
        if bot_type=="management" and user.onboarding_complete and cb.data.startswith("onboard_"): await svc.send_message(chat_id,"منوی مونس آماده‌ست 💙",menus.main_menu())
        return {"ok":True}
      if update.message is None: return {"ok":True}
      msg=update.message; chat_id=msg.chat.id; sender=msg.from_user; user=onboarding.get_or_create_user(db,sender.id,sender.first_name or sender.username,sender.language_code); text=(msg.text or "").strip()
      if bot_type=="chat":
        settings=get_settings()
        if not user.onboarding_complete and not settings.simple_chat_mode:
          u=(settings.telegram_management_bot_username or "MonesBot"); u=u if u.startswith('@') else '@'+u
          db.commit(); await svc.send_message(chat_id,f"برای شروع، اول باید پارتنر دیجیتالت رو بسازی 💙\nاز ربات مدیریت مونس شروع کن:\n\n{u}"); return {"ok":True}
        if text=="/start": db.commit(); await svc.send_message(chat_id,"سلام 💙\nمن اینجام. هرچی تو دلت هست بهم بگو."); return {"ok":True}
        if not text: return {"ok":True}
        if settings.simple_chat_mode:
          response=await handle_simple_chat(db,user,text)
        else:
          response=await orchestrator.handle_message(db,user,text)
        await svc.send_message(chat_id,response)
        if settings.simple_chat_mode:
          db.commit(); return {"ok":True}
        usage=orchestrator.subscriptions.get_or_create_today_usage(db,user); state=ensure_relationship(user.id,user.relationship_state); emotion=detect_emotion(text); ctx=stickers.context_from_message(text,response,state.stage)
        if stickers.should_send_sticker(db,ctx,state,emotion.value,usage,text):
          item=stickers.select_sticker(db,ctx,state,emotion.value,user.partner_personality_type)
          if item: usage.daily_stickers_sent += 1; db.commit(); await svc.send_sticker(chat_id,item.telegram_file_id)
        return {"ok":True}
      # management messages
      if _is_admin(sender.id) and user.admin_state:
        await _handle_admin_state(db,user,text,svc,chat_id); db.commit(); return {"ok":True}
      if _is_admin(sender.id) and text=="/addsticker": user.admin_state="addsticker:awaiting_sticker"; db.commit(); await svc.send_message(chat_id,"استیکر رو بفرست تا file_id ذخیره بشه."); return {"ok":True}
      if _is_admin(sender.id) and user.admin_state=="addsticker:awaiting_sticker" and msg.sticker:
        user.admin_state=f"addsticker:label:{msg.sticker.file_id}:{msg.sticker.emoji or ''}:{msg.sticker.set_name or ''}"; db.commit(); await svc.send_message(chat_id,"برچسب استیکر رو بنویس."); return {"ok":True}
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
      coins=int(text); target=rec.user; wallet=wallets.credit(db,target,coins,"manual_payment_approved",{"receipt_id":rid,"admin_id":user.telegram_id}); rec.status="approved"; rec.admin_id=user.telegram_id; rec.reviewed_at=datetime.utcnow(); user.admin_state=None
      await svc.send_message(chat_id,f"پرداخت تایید شد ✅\n{coins} سکه به کیف پول کاربر اضافه شد."); await svc.send_message(target.telegram_id,f"پرداختت تایید شد ✅\n{coins} سکه به کیف پولت اضافه شد.\n\nموجودی فعلی: {wallet.balance_coins} سکه")
    elif st.startswith("awaiting_payment_reject_reason:"):
      rid=int(st.split(":",1)[1]); rec=db.get(PaymentReceipt,rid)
      if rec and rec.status=="pending": rec.status="rejected"; rec.admin_id=user.telegram_id; rec.admin_note=text; rec.reviewed_at=datetime.utcnow(); await svc.send_message(rec.user.telegram_id,f"رسید پرداختت تایید نشد ❌\nدلیل: {text}\n\nاگر فکر می‌کنی اشتباهی شده، با پشتیبانی تماس بگیر.")
      user.admin_state=None; await svc.send_message(chat_id,"پرداخت رد شد.")
    elif st.startswith("addsticker:label:"):
      _,_,fid,emoji,setname=st.split(":",4); pack=None
      if setname:
        from sqlalchemy import select
        pack=db.scalar(select(StickerPack).where(StickerPack.telegram_set_name==setname)) or StickerPack(name=setname,telegram_set_name=setname); db.add(pack); db.flush()
      db.add(StickerItem(pack_id=pack.id if pack else None,telegram_file_id=fid,emoji=emoji or None,label=text,usage_context="comfort",relationship_stage_min="STRANGER")); user.admin_state=None; await svc.send_message(chat_id,"استیکر ذخیره شد ✅ (context پیش‌فرض: comfort)")

async def _handle_callback(db,user,data,telegram_id,bot_type):
 if bot_type=="chat": return "این ربات فقط برای چته 💙",None
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
 if data.startswith("admin_payment_approve:") and _is_admin(telegram_id): user.admin_state=f"awaiting_payment_approval_amount:{data.split(':')[1]}"; return "چند سکه به کیف پول کاربر اضافه بشه؟",None
 if data.startswith("admin_payment_reject:") and _is_admin(telegram_id): user.admin_state=f"awaiting_payment_reject_reason:{data.split(':')[1]}"; return "دلیل رد پرداخت رو بنویس.\nاگر دلیل خاصی نداری، بنویس: رد",None
 if data in {"settings_reset_memory","settings_delete_data"}: return menus.settings_placeholder(),None
 return "این گزینه معتبر نیست؛ لطفاً دوباره از منو انتخاب کن 💙",None
