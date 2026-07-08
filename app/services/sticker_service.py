import hashlib
import logging
import random
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.relationship import relationship_stage_rank
from app.models.sticker import StickerItem
from app.models.subscription import DailyUsage
from app.services.settings_service import SettingsService
from app.services.telegram_service import TelegramService

MOODS = ["warm","playful","affectionate","teasing","shy","upset","cold","sleepy","laughing","heart","kiss","comfort","default"]
SERIOUS = re.compile(r"(خودکشی|مرگ|بمیرم|افسرده|تجاوز|آسیب)")
ORDER = {"STRANGER":0,"WARM":1,"CLOSE":2,"PARTNER":3,"LOVER":4,"FAMILIAR":1,"FRIEND":2,"ROMANTIC":3}
CATEGORIES = {"normal", "romantic", "playful", "adult_intimacy"}
GENDERS = {"female", "male", "neutral"}
UNDERAGE_VALUES = {"زیر ۱۸", "زیر18", "under18", "under_18", "minor", "underage"}
ADULT_ALLOWED_STAGES = {"PARTNER", "LOVER", "ROMANTIC", "INTIMATE", "BONDED"}


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
        rows = db.scalars(select(StickerItem).where(StickerItem.is_active==True, StickerItem.enabled==True, StickerItem.usage_context==mood)).all()
        if not rows and mood != "default":
            rows = db.scalars(select(StickerItem).where(StickerItem.is_active==True, StickerItem.enabled==True, StickerItem.usage_context=="default")).all()
        if not rows: return None
        expanded=[]
        for r in rows: expanded += [r]*max(1, int(r.weight or 1))
        seed=int(hashlib.sha256((mood+str(len(expanded))).encode()).hexdigest(),16)
        item=expanded[seed%len(expanded)]
        logging.getLogger(__name__).info("STICKER_DECISION mood=%s selected=%s fallback=%s", mood, item.id, mood != item.usage_context)
        return item

    def select_sticker(self, db: Session, context: str, state, emotion: str, personality: str|None):
        stage=getattr(state,"stage","STRANGER")
        rows=db.scalars(select(StickerItem).where(StickerItem.is_active==True, StickerItem.enabled==True, StickerItem.usage_context==context)).all()
        candidates=[r for r in rows if not r.relationship_stage_min or ORDER.get(r.relationship_stage_min,0)<=ORDER.get(stage,0)]
        if personality: candidates=[r for r in candidates if not r.personality_match or r.personality_match==personality] or candidates
        if not candidates: return None
        expanded=[]
        for r in candidates: expanded += [r]*max(1,r.weight)
        return expanded[int(hashlib.sha256(context.encode()).hexdigest(),16)%len(expanded)]

    def select_contextual_sticker(self, db: Session, user, conversation_context: dict[str, Any] | str | None, emotion: str | None, category: str | None = "normal") -> StickerItem | None:
        ctx = conversation_context if isinstance(conversation_context, dict) else {"text": str(conversation_context or "")}
        requested_category = category or ctx.get("category") or "normal"
        if requested_category not in CATEGORIES:
            requested_category = "normal"
        if requested_category == "adult_intimacy" and not self._adult_stickers_allowed(user, ctx):
            return None

        usage = ctx.get("usage") or self._today_usage(db, getattr(user, "id", None))
        partner_gender = self._partner_gender(user)
        stage = self._stage(user, ctx)
        text = str(ctx.get("text") or ctx.get("message") or "")
        moods = {str(x).lower() for x in [emotion, ctx.get("mood"), getattr(user, "current_mood", None)] if x}

        rows = db.scalars(select(StickerItem).where(StickerItem.is_active==True, StickerItem.enabled==True, StickerItem.category==requested_category)).all()
        candidates: list[StickerItem] = []
        for item in rows:
            if (item.gender_target or "neutral") not in {partner_gender, "neutral"}:
                continue
            if not self._stage_allowed(item, stage):
                continue
            if self._over_daily_limit(item, usage):
                continue
            if not self._probability_passes(item, ctx):
                continue
            candidates.append(item)
        if not candidates:
            return None

        def score(item: StickerItem) -> float:
            total = max(1, int(item.weight or 1))
            if item.mood and item.mood.lower() in moods: total += 8
            if item.usage_context and item.usage_context.lower() in moods: total += 4
            if item.trigger_emojis and any(e in text for e in item.trigger_emojis): total += 10
            if item.relationship_stages and stage in item.relationship_stages: total += 6
            if item.relationship_stage_min and ORDER.get(item.relationship_stage_min, 0) <= ORDER.get(stage, 0): total += 2
            total += float(item.probability or 0) * 3
            return total

        candidates.sort(key=lambda i: (score(i), i.created_at or datetime.min, i.id or 0), reverse=True)
        return candidates[0]

    def _today_usage(self, db: Session, user_id: int | None) -> DailyUsage | None:
        if not user_id: return None
        return db.scalar(select(DailyUsage).where(DailyUsage.user_id == user_id, DailyUsage.date == date.today()))

    def _partner_gender(self, user) -> str:
        raw = str(getattr(user, "partner_gender", "") or "").lower()
        if raw in {"female", "woman", "girl", "دختر", "زن"}: return "female"
        if raw in {"male", "man", "boy", "پسر", "مرد"}: return "male"
        return "neutral"

    def _stage(self, user, ctx: dict[str, Any]) -> str:
        rel = ctx.get("relationship") or getattr(user, "relationship_state", None)
        return str(ctx.get("relationship_stage") or getattr(rel, "stage", None) or "STRANGER").upper()

    def _stage_allowed(self, item: StickerItem, stage: str) -> bool:
        if item.relationship_stages and stage not in [str(s).upper() for s in item.relationship_stages]:
            return False
        if item.relationship_stage_min:
            return relationship_stage_rank(stage) >= relationship_stage_rank(item.relationship_stage_min)
        return True

    def _adult_stickers_allowed(self, user, ctx: dict[str, Any]) -> bool:
        if not bool(ctx.get("adult_chat_mode") or getattr(user, "adult_chat_mode", False)):
            return False
        if str(getattr(user, "partner_age_range", "") or "").lower() in UNDERAGE_VALUES:
            return False
        adult_setting = bool(ctx.get("adult_user_eligible") or getattr(user, "mature_intimacy_unlocked", False) or getattr(user, "intimacy_override_max", False))
        if not adult_setting:
            return False
        stage = self._stage(user, ctx)
        return stage in ADULT_ALLOWED_STAGES or bool(getattr(user, "intimacy_override_max", False))

    def _over_daily_limit(self, item: StickerItem, usage: DailyUsage | None) -> bool:
        return bool(item.daily_limit is not None and usage and usage.daily_stickers_sent >= item.daily_limit)

    def _probability_passes(self, item: StickerItem, ctx: dict[str, Any]) -> bool:
        probability = max(0.0, min(1.0, float(item.probability if item.probability is not None else 1.0)))
        if probability >= 1: return True
        if probability <= 0: return False
        seed_src = f"{ctx.get('text','')}:{ctx.get('mood','')}:{item.id}:{item.key}"
        seed = int(hashlib.sha256(seed_src.encode()).hexdigest(), 16)
        return (seed % 10000) / 10000 <= probability

    async def send_sticker(self, chat_id: int, sticker_file_id: str, bot_type: str="chat"):
        await TelegramService(bot_type=bot_type).send_sticker(chat_id, sticker_file_id)
