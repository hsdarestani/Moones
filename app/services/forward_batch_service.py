"""Redis-backed debounce for Telegram forward bursts."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)
QUIET_SECONDS = 1.5
MAX_WINDOW_SECONDS = 5.0
MAX_ITEMS = 10
MAX_TEXT_CHARS = 6000
TTL_SECONDS = 30


def is_forwarded_message(message: Any) -> bool:
    return any(getattr(message, field, None) is not None for field in (
        "forward_origin", "forward_from", "forward_from_chat", "forward_sender_name", "forward_date"
    ))


def compact_forward_item(message: Any, update_id: int) -> dict[str, Any]:
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    media = None
    if getattr(message, "photo", None): media = "photo"
    elif getattr(message, "voice", None): media = "voice"
    elif getattr(message, "audio", None): media = "audio"
    elif getattr(message, "sticker", None): media = "sticker"
    elif getattr(message, "document", None): media = "document"
    return {"update_id": update_id, "message_id": message.message_id,
            "text": text[:MAX_TEXT_CHARS], "media_type": media}


def format_forward_batch(items: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    remaining = MAX_TEXT_CHARS
    for index, item in enumerate(sorted(items, key=lambda x: (x["message_id"], x["update_id"])), 1):
        marker = f"[Forwarded message {index}]"
        body = (item.get("text") or "").strip()
        media = item.get("media_type")
        if media:
            body = f"[{media}]" + (f"\n{body}" if body else "")
        body = body or "[message without textual content]"
        block = f"{marker}\n{body}"
        if len(block) > remaining:
            block = block[:remaining]
        if not block: break
        blocks.append(block); remaining -= len(block) + 2
        if remaining <= 0: break
    return "\n\n".join(blocks)


class ForwardBatchService:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    @staticmethod
    def key(bot_type: str, chat_id: int, user_id: int) -> str:
        return f"forward-batch:v2:{bot_type}:{chat_id}:{user_id}"

    @staticmethod
    def _identity(key: str) -> tuple[str, str]:
        parts = key.rsplit(":", 2)
        return (parts[-2], parts[-1]) if len(parts) >= 3 else ("-", "-")

    async def buffer(self, key: str, item: dict[str, Any]) -> tuple[bool, int, bool]:
        dedupe = f"{key}:dedupe:{item['update_id']}:{item['message_id']}"
        if not await self.redis.set(dedupe, "1", ex=TTL_SECONDS, nx=True):
            chat_id, user_id = self._identity(key)
            logger.info("FORWARD_BATCH_DEDUPED user_id=%s chat_id=%s item_count=%s",
                        user_id, chat_id, int(await self.redis.llen(key)))
            return False, int(await self.redis.llen(key)), False
        now = time.time()
        pipe = self.redis.pipeline(transaction=True)
        pipe.rpush(key, json.dumps(item, ensure_ascii=False))
        pipe.expire(key, TTL_SECONDS)
        pipe.set(f"{key}:latest", now, ex=TTL_SECONDS)
        pipe.set(f"{key}:first", now, ex=TTL_SECONDS, nx=True)
        pipe.incrby(f"{key}:chars", len(item.get("text") or ""))
        pipe.expire(f"{key}:chars", TTL_SECONDS)
        results = await pipe.execute()
        count = int(results[0])
        first = float(await self.redis.get(f"{key}:first") or now)
        combined_chars = int(results[4])
        force = count >= MAX_ITEMS or combined_chars >= MAX_TEXT_CHARS or now - first >= MAX_WINDOW_SECONDS
        return True, count, force

    async def flush(self, key: str, callback: Callable[[list[dict[str, Any]]], Awaitable[None]]) -> bool:
        lock = f"{key}:lock"
        if not await self.redis.set(lock, "1", ex=15, nx=True):
            return False
        try:
            raw = await self.redis.lrange(key, 0, MAX_ITEMS - 1)
            if not raw: return False
            items = [json.loads(x) for x in raw]
            await self.redis.delete(key, f"{key}:latest", f"{key}:first", f"{key}:chars")
            chat_id, user_id = self._identity(key)
            logger.info("FORWARD_BATCH_FLUSH_STARTED user_id=%s chat_id=%s item_count=%s",
                        user_id, chat_id, len(items))
            await callback(items)
            logger.info("FORWARD_BATCH_FLUSHED user_id=%s chat_id=%s item_count=%s",
                        user_id, chat_id, len(items))
            return True
        except Exception:
            chat_id, user_id = self._identity(key)
            logger.exception("FORWARD_BATCH_FAILED user_id=%s chat_id=%s item_count=%s",
                             user_id, chat_id, len(raw) if 'raw' in locals() else 0)
            await self.redis.delete(key, f"{key}:latest", f"{key}:first", f"{key}:chars")
            raise
        finally:
            await self.redis.delete(lock)

    async def flush_after_quiet(self, key: str, callback: Callable[[list[dict[str, Any]]], Awaitable[None]]) -> None:
        while True:
            latest = await self.redis.get(f"{key}:latest")
            first = await self.redis.get(f"{key}:first")
            if latest is None: return
            now = time.time()
            wait = min(QUIET_SECONDS - (now - float(latest)),
                       MAX_WINDOW_SECONDS - (now - float(first or latest)))
            if wait <= 0: break
            await asyncio.sleep(wait)
        await self.flush(key, callback)
