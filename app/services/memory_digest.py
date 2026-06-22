from __future__ import annotations
import logging, re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.message import Message
from app.models.memory import MemoryItem
from app.models.user import User
from app.models.settings import AppSetting

logger=logging.getLogger(__name__)
IMPORTANT=("دوست دارم","دوست ندارم","ترجیح می","یادت باشه","لحن","رسمی","خودمونی","اسمم","مرز","قول","قرار","برنامه","ناراحت","مهم")
SKIP=("سلام","خوبی","چطوری","هه","😂","😅")
@dataclass
class MemoryCandidate:
    content:str; category:str="preference"; importance:int=3; confidence:float=0.75

def extract_memory_candidates(messages:list[Message])->list[MemoryCandidate]:
    out=[]
    for m in messages:
        if m.role!="user": continue
        txt=re.sub(r"\s+"," ",(m.content or "")).strip()
        if len(txt)<12 or any(txt==s for s in SKIP): continue
        if any(k in txt for k in IMPORTANT):
            sent=txt[:180]
            if "رسمی" in txt or "خودمونی" in txt or "لحن" in txt: cat="tone_preference"; imp=4
            elif "قول" in txt or "قرار" in txt or "برنامه" in txt: cat="ongoing_plan"; imp=3
            else: cat="user_preference"; imp=3
            if not sent.startswith("کاربر"):
                sent="کاربر گفته/ترجیح داده: "+sent
            out.append(MemoryCandidate(sent,cat,imp,0.8))
    return out[:12]

def _similar(a,b): return SequenceMatcher(None,a,b).ratio()

def save_memory_candidates(db:Session,user_id:int,candidates:list[MemoryCandidate],source_date:date|None=None,max_memories:int=100)->dict[str,int]:
    stats={"saved":0,"merged":0,"skipped":0}
    existing=db.scalars(select(MemoryItem).where(MemoryItem.user_id==user_id)).all()
    for c in candidates:
        content=c.content[:220]
        match=max(existing, key=lambda x:_similar(x.content,content), default=None)
        if match and _similar(match.content,content)>0.72:
            match.content=content if len(content)>len(match.content or "") else match.content
            match.importance_score=max(match.importance_score or 0, c.importance/5)
            stats["merged"]+=1; continue
        db.add(MemoryItem(user_id=user_id,type=c.category,content=content,importance_score=c.importance/5))
        stats["saved"]+=1
    all_items=db.scalars(select(MemoryItem).where(MemoryItem.user_id==user_id).order_by(MemoryItem.importance_score.desc(),MemoryItem.created_at.desc())).all()
    for item in all_items[max_memories:]: db.delete(item); stats["skipped"]+=1
    db.flush(); return stats

def run_daily_memory_digest(db:Session,digest_date:date|None=None,user_id:int|None=None)->dict[str,int]:
    digest_date=digest_date or (datetime.utcnow().date()-timedelta(days=1))
    start=datetime.combine(digest_date,time.min); end=datetime.combine(digest_date,time.max)
    logger.info("DAILY_MEMORY_DIGEST_STARTED date=%s", digest_date.isoformat())
    users=[db.get(User,user_id)] if user_id else db.scalars(select(User).join(Message).where(Message.created_at>=start,Message.created_at<=end).distinct()).all()
    totals={"users":0,"saved":0,"merged":0,"skipped":0}
    for u in [x for x in users if x]:
        msgs=db.scalars(select(Message).where(Message.user_id==u.id,Message.created_at>=start,Message.created_at<=end).order_by(Message.created_at.asc())).all()
        if not msgs:
            logger.info("DAILY_MEMORY_DIGEST_SKIPPED user_id=%s reason=empty_day",u.id); continue
        cands=extract_memory_candidates(msgs)
        stats=save_memory_candidates(db,u.id,cands,digest_date) if cands else {"saved":0,"merged":0,"skipped":0}
        key=f"memory.last_digest_at.{u.id}"
        row=db.scalar(select(AppSetting).where(AppSetting.key==key)) or AppSetting(key=key,value="",value_type="string")
        db.add(row); row.value=datetime.utcnow().isoformat()
        logger.info("DAILY_MEMORY_DIGEST_USER user_id=%s messages=%s candidates=%s saved=%s merged=%s skipped=%s",u.id,len(msgs),len(cands),stats['saved'],stats['merged'],stats['skipped'])
        totals["users"]+=1
        for k in ("saved","merged","skipped"): totals[k]+=stats[k]
    db.flush(); return totals
