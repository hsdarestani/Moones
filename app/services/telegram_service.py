import logging
import re
import httpx

logger = logging.getLogger(__name__)

def _safe_url(url: str) -> str:
    return re.sub(r"/bot[^/]+/", "/bot<redacted>/", url or "")

def _safe_body(text: str) -> str:
    return re.sub(r"bot[0-9A-Za-z:_-]+", "bot<redacted>", (text or "")[:500])
from app.core.config import get_settings

class TelegramService:
    def __init__(self, bot_type: str = "management") -> None:
        self.settings = get_settings()
        if bot_type == "chat":
            self.token = self.settings.telegram_chat_bot_token
        else:
            self.token = self.settings.management_bot_token
        self.base_url = f"https://api.telegram.org/bot{self.token}"


    async def get_chat_member_status(self, user_id: int, channel_username: str) -> str:
        if not self.token:
            raise RuntimeError("missing_token")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{self.base_url}/getChatMember", json={"chat_id": channel_username, "user_id": user_id})
        if response.status_code >= 400:
            logger.error("Telegram getChatMember failed url=%s status=%s body=%s", _safe_url(f"{self.base_url}/getChatMember"), response.status_code, _safe_body(response.text))
            response.raise_for_status()
        data = response.json()
        return ((data.get("result") or {}).get("status") or "")
    async def send_text(self, chat_id: int, text: str, reply_markup: dict | None = None) -> int | None:
        if not self.token: return None
        payload={"chat_id":chat_id,"text":text}
        if reply_markup: payload["reply_markup"]=reply_markup
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{self.base_url}/sendMessage", json=payload)
        if response.status_code >= 400:
            logger.error("Telegram sendMessage failed status=%s body=%s", response.status_code, response.text)
            response.raise_for_status()
        data = response.json()
        return ((data.get("result") or {}).get("message_id"))
    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> int | None:
        return await self.send_text(chat_id, text, reply_markup)
    async def send_voice(self, chat_id: int, ogg_bytes: bytes, caption: str | None = None) -> None:
        if not self.token: return
        data={"chat_id": str(chat_id)}
        # Never attach response text below voice notes.
        files={"voice": ("voice.ogg", ogg_bytes, "audio/ogg")}
        async with httpx.AsyncClient(timeout=10) as client: await client.post(f"{self.base_url}/sendVoice", data=data, files=files)
    async def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> None:
        if not self.token: return
        payload={"chat_id":chat_id,"message_id":message_id,"text":text}
        if reply_markup: payload["reply_markup"]=reply_markup
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{self.base_url}/editMessageText", json=payload)
        if response.status_code == 400:
            logger.error("Telegram editMessageText failed url=%s status=%s body=%s", _safe_url(f"{self.base_url}/editMessageText"), response.status_code, _safe_body(response.text))
            await self.send_message(chat_id, text, reply_markup)
        elif response.status_code >= 400:
            logger.error("Telegram editMessageText failed url=%s status=%s body=%s", _safe_url(f"{self.base_url}/editMessageText"), response.status_code, _safe_body(response.text))
            await self.send_message(chat_id, text, reply_markup)
    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        if not self.token: return
        payload={"callback_query_id":callback_query_id}
        if text: payload["text"]=text
        async with httpx.AsyncClient(timeout=10) as client: await client.post(f"{self.base_url}/answerCallbackQuery", json=payload)
    async def send_photo(self, chat_id: int, photo: str, caption: str|None=None, reply_markup: dict|None=None) -> None:
        if not self.token: return
        payload={"chat_id":chat_id,"photo":photo}
        if caption: payload["caption"]=caption
        if reply_markup: payload["reply_markup"]=reply_markup
        async with httpx.AsyncClient(timeout=10) as client: await client.post(f"{self.base_url}/sendPhoto", json=payload)
    async def send_document(self, chat_id: int, document: str, caption: str|None=None, reply_markup: dict|None=None) -> None:
        if not self.token: return
        payload={"chat_id":chat_id,"document":document}
        if caption: payload["caption"]=caption
        if reply_markup: payload["reply_markup"]=reply_markup
        async with httpx.AsyncClient(timeout=10) as client: await client.post(f"{self.base_url}/sendDocument", json=payload)
    async def send_sticker(self, chat_id: int, sticker: str) -> None:
        if not self.token: return
        async with httpx.AsyncClient(timeout=10) as client: await client.post(f"{self.base_url}/sendSticker", json={"chat_id":chat_id,"sticker":sticker})
