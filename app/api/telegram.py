from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.engine.orchestrator import ConversationOrchestrator
from app.services.onboarding_service import OnboardingService
from app.services.telegram_service import TelegramService

router = APIRouter(prefix="/telegram", tags=["telegram"])
orchestrator = ConversationOrchestrator()
onboarding = OnboardingService()
telegram_service = TelegramService()


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
    if update.callback_query is not None and update.callback_query.data and update.callback_query.message:
        callback = update.callback_query
        sender = callback.from_user
        user = onboarding.get_or_create_user(db, sender.id, sender.first_name or sender.username, sender.language_code)
        reply = onboarding.handle_callback(user, callback.data)
        db.commit()
        await telegram_service.answer_callback_query(callback.id)
        await telegram_service.edit_message(callback.message.chat.id, callback.message.message_id, reply.text, reply.reply_markup)
        return {"ok": True}

    if update.message is None or not update.message.text:
        return {"ok": True}
    sender = update.message.from_user
    display_name = sender.first_name or sender.username
    user = onboarding.get_or_create_user(db, sender.id, display_name, sender.language_code)
    onboarding_reply = onboarding.handle_text(user, update.message.text)
    if onboarding_reply or not user.onboarding_complete:
        reply = onboarding_reply or onboarding.start(user)
        db.commit()
        await telegram_service.send_message(update.message.chat.id, reply.text, reply.reply_markup)
        return {"ok": True}

    response = await orchestrator.handle_message(db, user, update.message.text)
    await telegram_service.send_message(update.message.chat.id, response)
    return {"ok": True}
