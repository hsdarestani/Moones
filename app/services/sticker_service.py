import hashlib, re
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.sticker import StickerItem
from app.models.subscription import DailyUsage
from app.services.settings_service import SettingsService
from app.services.telegram_service import TelegramService

MOODS = ["warm","playful","affectionate","teasing","shy","upset","cold","sleepy","laughing","heart","kiss","comfort","default"]
SERIOUS = re.compile(r"(خودکشی|مرگ|بمیرم|افسرده|تجاوز|آسیب)")
ORDER = {"STRANGER":0,"WARM":1,"CLOSE":2,"PARTNER":3,"LOVER":4,"FAMILIAR":1,"FRIEND":2,"ROMANTIC":3}
class StickerService:
    MOODS = MOODS
    def __init__(self): self.settings=SettingsService()
    def context_from_message(self, msg: str, response: str, stage: str) -> str:
        m=msg.lower()
        if any(x in msg for x in ["سلام","درود"]): return "greeting"
        if any(x in msg for x in ["شب بخیر","خواب"]): return "goodnight"
        if any(x in msg for x in ["دلم تنگ","دلتنگ"]): return "miss_you"
        if any(x in msg for x in ["غمگین","ناراحت","تنها"]): return "sad_support"
        if any(x in msg for x in ["خخ","😂","شوخی"]): return "playful"
        if any(x in msg for x in ["دوستت دارم","عاشق"]): return "romantic" if stage in {"PARTNER","LOVER","ROMANTIC"} else "affection"
        return "comfort"
    def should_send_sticker(self, db: Session, context: str, state, emotion: str, usage: DailyUsage|None, user_message: str="") -> bool:
        if not self.settings.get_bool(db,"stickers.enabled",True) or SERIOUS.search(user_message): return False
        if usage and usage.daily_stickers_sent >= self.settings.get_int(db,"stickers.max_per_day_per_user",10): return False
        seed=int(hashlib.sha256((context+emotion+str(getattr(state,'stage',''))).encode()).hexdigest(),16)
        return (seed % 100)/100 <= self.settings.get_float(db,"stickers.probability",0.12)
    def random_by_mood(self, db: Session, mood: str):
        rows = db.scalars(select(StickerItem).where(StickerItem.is_active==True, StickerItem.usage_context==mood)).all()
        if not rows and mood != "default":
            rows = db.scalars(select(StickerItem).where(StickerItem.is_active==True, StickerItem.usage_context=="default")).all()
        if not rows: return None
        expanded=[]
        for r in rows: expanded += [r]*max(1, int(r.weight or 1))
        seed=int(hashlib.sha256((mood+str(len(expanded))).encode()).hexdigest(),16)
        item=expanded[seed%len(expanded)]
        import logging; logging.getLogger(__name__).info("STICKER_DECISION mood=%s selected=%s fallback=%s", mood, item.id, mood != item.usage_context)
        return item

    def select_sticker(self, db: Session, context: str, state, emotion: str, personality: str|None):
        stage=getattr(state,"stage","STRANGER")
        rows=db.scalars(select(StickerItem).where(StickerItem.is_active==True, StickerItem.usage_context==context)).all()
        candidates=[r for r in rows if not r.relationship_stage_min or ORDER.get(r.relationship_stage_min,0)<=ORDER.get(stage,0)]
        if personality: candidates=[r for r in candidates if not r.personality_match or r.personality_match==personality] or candidates
        if not candidates: return None
        expanded=[]
        for r in candidates: expanded += [r]*max(1,r.weight)
        return expanded[int(hashlib.sha256(context.encode()).hexdigest(),16)%len(expanded)]
    async def send_sticker(self, chat_id: int, sticker_file_id: str, bot_type: str="chat"):
        await TelegramService(bot_type=bot_type).send_sticker(chat_id, sticker_file_id)
