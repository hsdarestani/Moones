from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.engine.orchestrator import ConversationOrchestrator
from app.services.telegram_service import TelegramService

router = APIRouter(prefix="/telegram", tags=["telegram"])
orchestrator = ConversationOrchestrator()
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


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None


@router.post("/webhook")
async def telegram_webhook(update: TelegramUpdate, request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    if update.message is None or not update.message.text:
        return {"ok": True}
    sender = update.message.from_user
    display_name = sender.first_name or sender.username
    response = await orchestrator.handle_message(db, sender.id, display_name, update.message.text)
    await telegram_service.send_message(update.message.chat.id, response)
    return {"ok": True}
