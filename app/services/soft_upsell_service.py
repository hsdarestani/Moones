from __future__ import annotations
import logging, random
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.user import User
from app.services.subscription_service import SubscriptionService
from app.services.bot_link_service import management_bot_keyboard

logger = logging.getLogger(__name__)
FREE_PLANS={"free","daily","trial",None,""}
SENSITIVE_MOODS={"sad","crisis","angry","cold","slightly_upset"}
SOFT_UPSELL_MESSAGES=[
    {"text":"برای اینکه وسط گفتگو موجودیت تموم نشه، می‌تونی از ربات مدیریت موجودی کیف پولت رو ببینی یا شارژش کنی 🌙", "cta":"مشاهده کیف پول", "start":"wallet"},
    {"text":"از بخش افزودنی‌ها می‌تونی قابلیت‌هایی مثل دریافت عکس از مونس رو فعال کنی.", "cta":"مشاهده افزودنی‌ها", "start":"addons"},
]

class SoftUpsellSuggestion(dict):
    @property
    def text(self): return self["text"]
    @property
    def cta_label(self): return self["cta"]
    @property
    def management_start(self): return self["start"]
    def __str__(self): return self["text"]


class SoftUpsellService:
    def __init__(self): self.subs=SubscriptionService()
    def eligible(self, db:Session, user:User, now:datetime|None=None) -> tuple[bool,str]:
        now=now or datetime.utcnow(); plan=self.subs.active_plan_code(db,user)
        if plan not in FREE_PLANS: return False,"paid_plan"
        if getattr(user,"proactive_blocked",False): return False,"blocked"
        if getattr(user,"proactive_messages_enabled",True) is False: return False,"opt_out"
        if (getattr(user,"current_mood","") or "").lower() in SENSITIVE_MOODS: return False,"sensitive_mood"
        last=getattr(user,"last_soft_upsell_at",None)
        if last and last > now - timedelta(hours=48): return False,"cooldown"
        # deterministic eligibility after 48h; jitter is applied by random send chance in live flow.
        return True,"eligible"
    def choose_message(self) -> str:
        suggestion=SoftUpsellSuggestion(random.choice(SOFT_UPSELL_MESSAGES)); logger.info("SOFT_UPSELL_SELECTED user_id=%s", "-"); return suggestion
    def mark_sent(self, db:Session, user:User, now:datetime|None=None) -> None:
        user.last_soft_upsell_at=now or datetime.utcnow(); db.flush(); logger.info("SOFT_UPSELL_SENT user_id=%s", user.id)
    def keyboard(self):
        suggestion=self.choose_message(); return management_bot_keyboard(suggestion.cta_label, start=suggestion.management_start)
