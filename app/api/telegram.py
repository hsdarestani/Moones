import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.engine.orchestrator import ConversationOrchestrator
from app.services.bot_menu_service import BotMenuService
from app.services.onboarding_service import OnboardingService
from app.services.telegram_service import TelegramService
from app.services.wallet_service import WalletService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])
orchestrator = ConversationOrchestrator()
onboarding = OnboardingService()
menus = BotMenuService()
wallets = WalletService()
telegram_service = TelegramService()
FALLBACK_ERROR_TEXT = "یه مشکلی پیش اومد 😅\nدوباره امتحان کن، من اینجام."


class TelegramUser(BaseModel):
    id: int
    first_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class TelegramChat(BaseModel):
    id: int


class TelegramMessage(BaseModel):
    message_id: int
    from_user: TelegramUser = Field(alias="from")
    chat: TelegramChat
    text: str | None = None


class TelegramCallbackQuery(BaseModel):
    id: str
    from_user: TelegramUser = Field(alias="from")
    message: TelegramMessage | None = None
    data: str | None = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


@router.post("/webhook")
async def telegram_webhook(update: TelegramUpdate, request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    chat_id: int | None = None
    try:
        if update.callback_query is not None and update.callback_query.data and update.callback_query.message:
            callback = update.callback_query
            chat_id = callback.message.chat.id
            sender = callback.from_user
            user = onboarding.get_or_create_user(db, sender.id, sender.first_name or sender.username, sender.language_code)
            await telegram_service.answer_callback_query(callback.id)
            text, markup = await _handle_callback(db, user, callback.data)
            db.commit()
            await telegram_service.edit_message(chat_id, callback.message.message_id, text, markup)
            if user.onboarding_complete:
                await telegram_service.send_message(chat_id, "منوی مونس آماده‌ست 💙", menus.main_menu())
            return {"ok": True}

        if update.message is None or not update.message.text:
            return {"ok": True}
        chat_id = update.message.chat.id
        sender = update.message.from_user
        display_name = sender.first_name or sender.username
        user = onboarding.get_or_create_user(db, sender.id, display_name, sender.language_code)
        text = update.message.text.strip()

        if text == "/start" and user.onboarding_complete:
            db.commit()
            await telegram_service.send_message(chat_id, "سلام، خوش برگشتی 💙\nاز منوی پایین هر بخش رو خواستی انتخاب کن.", menus.main_menu())
            return {"ok": True}

        onboarding_reply = onboarding.handle_text(user, text)
        if onboarding_reply or not user.onboarding_complete:
            reply = onboarding_reply or onboarding.intro()
            db.commit()
            await telegram_service.send_message(chat_id, reply.text, reply.reply_markup)
            return {"ok": True}

        menu_text, menu_markup, handled = menus.handle_menu_text(db, user, text)
        if handled:
            db.commit()
            await telegram_service.send_message(chat_id, menu_text, menu_markup or menus.main_menu())
            return {"ok": True}

        response = await orchestrator.handle_message(db, user, text)
        await telegram_service.send_message(chat_id, response, menus.main_menu())
        return {"ok": True}
    except Exception:
        logger.exception("Telegram webhook failed for update_id=%s", update.update_id)
        db.rollback()
        if chat_id is not None:
            try:
                await telegram_service.send_message(chat_id, FALLBACK_ERROR_TEXT, menus.main_menu())
            except Exception:
                logger.exception("Failed to send Telegram fallback error")
        return {"ok": True}


async def _handle_callback(db: Session, user, data: str) -> tuple[str, dict | None]:
    if data.startswith("onboard_") or data.startswith("onboarding:"):
        reply = onboarding.handle_callback(user, data)
        if user.onboarding_complete:
            wallets.get_or_create_wallet(db, user)
            onboarding.subscriptions.ensure_free_subscription(db, user)
        return reply.text, reply.reply_markup

    if data in {"sub_buy_daily", "sub_buy_weekly", "sub_buy_monthly", "sub_buy_premium"}:
        return menus.payment_placeholder(), None
    if data == "sub_status":
        return menus.subscription_status_text(db, user), None

    if data == "wallet_topup_menu":
        return menus.topup_text(), menus.topup_keyboard()
    if data in {"wallet_topup_100", "wallet_topup_500", "wallet_topup_1000"}:
        if not get_settings().enable_test_wallet_topup:
            return "افزایش موجودی به‌زودی فعال می‌شه.", None
        amount = int(data.rsplit("_", 1)[1])
        wallet = wallets.credit(db, user, amount, reason="test_topup", metadata={"source": "telegram_test_topup"})
        return f"موجودی تستی اضافه شد ✅\nموجودی فعلی: {wallet.balance_coins} سکه", None
    if data == "wallet_history":
        return menus.history_text(db, user), None

    if data == "partner_edit_prompt":
        return "برای ویرایش پارتنر، باید دوباره فرایند ساخت رو انجام بدی.\nادامه می‌دی؟", menus.partner_edit_prompt_keyboard()
    if data == "partner_edit_confirm":
        reply = onboarding.reset_for_edit(user)
        return reply.text, reply.reply_markup
    if data == "partner_edit_cancel":
        return "باشه، پارتنرت بدون تغییر می‌مونه 💙", None

    if data in {"settings_reset_memory", "settings_delete_data"}:
        return menus.settings_placeholder(), None

    logger.warning("Unhandled Telegram callback: %s", data)
    return "این گزینه معتبر نیست؛ لطفاً دوباره از منو انتخاب کن 💙", None
