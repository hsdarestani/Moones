from __future__ import annotations
import logging
from app.services.telegram_service import TelegramService
THRESHOLDS = [20,5,0]
logger=logging.getLogger(__name__)
class LowBalanceNotificationService:
    async def maybe_notify(self, db, user, wallet):
        level = next((t for t in THRESHOLDS if wallet.balance_coins <= t), None)
        if level is None or (user.low_balance_notified_level is not None and user.low_balance_notified_level <= level): return False
        try:
            await TelegramService("management").send_message(user.telegram_id, f"موجودی سکه شما {wallet.balance_coins} است. برای ادامه استفاده، سکه اضافه کن ➕")
        except Exception as exc:
            logger.warning("LOW_BALANCE_NOTIFY_FAILED user_id=%s level=%s err=%s", user.id, level, type(exc).__name__)
        user.low_balance_notified_level=level; db.flush(); return True
