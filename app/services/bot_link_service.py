from urllib.parse import quote
from app.core.config import get_settings

_ALLOWED = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-')

def _username() -> str:
    s = get_settings()
    return (s.management_bot_username or s.telegram_management_bot_username or '').lstrip('@') or 'moonesaibot'

def _base() -> str:
    configured = (get_settings().management_bot_url or '').strip()
    if configured:
        return configured.rstrip('/')
    return f"https://t.me/{_username()}"

def _clean_start(start: str | None) -> str | None:
    if not start:
        return None
    start = str(start).strip()[:64]
    if not start or any(ch not in _ALLOWED for ch in start):
        return None
    return start

def management_bot_url(start: str | None = None) -> str:
    start = _clean_start(start)
    url = _base()
    return f"{url}?start={quote(start)}" if start else url

def management_bot_keyboard(text: str, *, start: str | None = None) -> dict:
    return {"inline_keyboard": [[{"text": text, "url": management_bot_url(start)}]]}
