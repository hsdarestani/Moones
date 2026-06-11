import asyncio
import httpx

from app.core.config import get_settings


class TelegramService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}"

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        if not self.settings.telegram_bot_token:
            return
        await self.send_typing(chat_id)
        await asyncio.sleep(min(1.2, max(0.2, len(text) / 600)))
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{self.base_url}/sendMessage", json=payload)

    async def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> None:
        if not self.settings.telegram_bot_token:
            return
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{self.base_url}/editMessageText", json=payload)

    async def answer_callback_query(self, callback_query_id: str) -> None:
        if not self.settings.telegram_bot_token:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{self.base_url}/answerCallbackQuery", json={"callback_query_id": callback_query_id})

    async def send_typing(self, chat_id: int) -> None:
        if not self.settings.telegram_bot_token:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{self.base_url}/sendChatAction", json={"chat_id": chat_id, "action": "typing"})
