import hashlib, re
from sqlalchemy.orm import Session
from app.services.settings_service import SettingsService

EMOJIS = ["💙","😅","🙂","🥺","😌","🤍","😄","🙃","✨","🫶","😔","😳","😏"]
SERIOUS = re.compile(r"(خودکشی|مرگ|بمیرم|افسرده|اضطراب شدید|تجاوز|آسیب)")
class EmojiEngine:
    def __init__(self): self.settings=SettingsService()
    def apply(self, db: Session, text: str, emotion_state: str, relationship_stage: str, partner_personality: str|None, user_message: str) -> str:
        if not self.settings.get_bool(db,"emoji.enabled",True) or SERIOUS.search(user_message): return text
        prob=self.settings.get_float(db,"emoji.probability",0.55); maxn=max(1,self.settings.get_int(db,"emoji.max_per_message",3))
        seed=int(hashlib.sha256((text+user_message).encode()).hexdigest(),16)
        if (seed % 100)/100 > prob: return text
        pool = ["🙂","😌","🤍"] if relationship_stage in {"STRANGER","FAMILIAR"} else EMOJIS
        if partner_personality == "playful_funny": pool += ["😄","🙃","😏"]
        count=min(maxn, 1 + seed % min(3,maxn))
        chosen=[]
        for i in range(count):
            e=pool[(seed >> (i*5)) % len(pool)]
            if e not in chosen: chosen.append(e)
        if any(e in text for e in EMOJIS): return text
        return text.rstrip()+" "+"".join(chosen)
