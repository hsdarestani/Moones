from __future__ import annotations
from datetime import datetime
import httpx


def support_photo_caption(*, media_ref: str, user_id: int, telegram_user_id: int | None, username: str | None, display_name: str | None, plan_name: str | None, source_message_id: int | None, caption_text: str | None = None) -> str:
    uname = f"@{username}" if username else "—"
    return f"📸 Moones Photo\n\nRef: {media_ref}\nDB user_id: {user_id}\nTG user_id: {telegram_user_id or '—'}\nUsername: {uname}\nName: {display_name or '—'}\nPlan: {plan_name or 'free'}\nSource message: {source_message_id or '—'}\n\nCaption:\n{caption_text or ''}"

async def forward_photo_to_support(*, bot_token: str, support_chat_id: int, source_chat_id: int, source_message_id: int, telegram_file_id: str, media_ref: str, user_id: int, telegram_user_id: int | None, username: str | None, display_name: str | None, plan_name: str | None, caption_text: str | None = None) -> dict:
    base = f"https://api.telegram.org/bot{bot_token}"
    caption = support_photo_caption(media_ref=media_ref, user_id=user_id, telegram_user_id=telegram_user_id, username=username, display_name=display_name, plan_name=plan_name, source_message_id=source_message_id, caption_text=caption_text)
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{base}/sendPhoto", json={"chat_id": support_chat_id, "photo": telegram_file_id, "caption": caption})
            if r.status_code < 400 and (r.json().get("ok") is True):
                return {"ok": True, "method": "sendPhoto", "message_id": ((r.json().get("result") or {}).get("message_id")), "forwarded_at": datetime.utcnow()}
            send_err = r.text[:500]
        except Exception as exc:
            send_err = str(exc)
        try:
            r = await client.post(f"{base}/copyMessage", json={"chat_id": support_chat_id, "from_chat_id": source_chat_id, "message_id": source_message_id, "caption": caption})
            if r.status_code < 400 and (r.json().get("ok") is True):
                return {"ok": True, "method": "copyMessage", "message_id": ((r.json().get("result") or {}).get("message_id")), "forwarded_at": datetime.utcnow()}
            copy_err = r.text[:500]
        except Exception as exc:
            copy_err = str(exc)
    return {"ok": False, "error": f"sendPhoto={send_err}; copyMessage={copy_err}"}
