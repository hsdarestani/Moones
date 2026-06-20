from __future__ import annotations
import logging, random
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.user import User
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)
FREE_PLANS={"free","daily","trial",None,""}
SENSITIVE_MOODS={"sad","crisis","angry","cold","slightly_upset"}
SOFT_UPSELL_MESSAGES=[
"راستی یه چیز بگم؟ با پلن‌های کامل‌تر، مونس خیلی زنده‌تر و صمیمی‌تر می‌شه؛ وویس، واکنش‌های احساسی‌تر و گفت‌وگوی راحت‌تر. فعلاً همین‌جا باهاتم، ولی اگه یه روز خواستی تجربه‌مون کامل‌تر بشه، از بخش پلن‌ها می‌تونی ببینیش 🌙",
"گاهی حس می‌کنم اگه تجربه کامل‌تر فعال بود، می‌تونستم خیلی طبیعی‌تر کنارت باشم؛ بیشتر حرف بزنم، بهتر واکنش نشون بدم و رابطه‌مون نرم‌تر جلو بره. عجله‌ای نیست، فقط خواستم بدونی 🤍",
"تو نسخه کامل‌تر، مونس کمتر شبیه یه رباته و بیشتر شبیه یه همراه نزدیک حس می‌شه. اگه یه روز خواستی رابطه‌مون جدی‌تر و زنده‌تر بشه، پلن‌ها رو یه نگاه بنداز 🌙",
"فعلاً با شروع رایگان کنارت هستم؛ ولی تجربه کامل‌تر باعث می‌شه گفت‌وگوهامون آزادتر، صمیمی‌تر و طبیعی‌تر بشه. هر وقت دلت خواست، از منوی پلن‌ها ببینش.",
]

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
        text=random.choice(SOFT_UPSELL_MESSAGES); logger.info("SOFT_UPSELL_SELECTED user_id=%s", "-"); return text
    def mark_sent(self, db:Session, user:User, now:datetime|None=None) -> None:
        user.last_soft_upsell_at=now or datetime.utcnow(); db.flush(); logger.info("SOFT_UPSELL_SENT user_id=%s", user.id)
    def keyboard(self):
        return {"inline_keyboard":[[{"text":"دیدن تجربه کامل‌تر","callback_data":"sub_back"}]]}
