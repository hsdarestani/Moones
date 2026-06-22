from __future__ import annotations
import logging, re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.message import Message
from app.models.style_audit import BotStyleAudit
from app.models.settings import AppSetting
logger=logging.getLogger(__name__)
PATTERNS=[("too_formal",r"نشان بده|ارسال کنم|دریافت کردم", "به‌جای نشان/ارسال/دریافت از نشون/بفرستم/گرفتم استفاده کن."),("voice_capability_refusal",r"نمی[‌ ]?تونم وویس بفرستم|امکان ارسال صوت ندارم", "محدودیت وویس را نگو؛ جواب طبیعی و گرم بده."),("sticker_limitation_mentioned",r"استیکر نمی[‌ ]?فرستم|نمی[‌ ]?تونم استیکر بفرستم|استیکر ندارم", "محدودیت استیکر را حذف کن و حس را با متن طبیعی نشان بده."),("robotic_self_reference",r"من یک مدل هوش مصنوعی هستم|به عنوان یک هوش مصنوعی", "هرگز از مدل/هوش مصنوعی بودن حرف نزن؛ در نقش پارتنر بمان.")]
LESSONS={"too_formal":"در چت احساسی فارسی، به‌جای واژه‌های رسمی مثل ارسال/دریافت/نشان از بفرستم/گرفتم/نشون استفاده کن.","voice_capability_refusal":"وقتی امکان وویس محدود است، درباره محدودیت حرف نزن؛ متن طبیعی و صمیمی جایگزین بده.","sticker_limitation_mentioned":"وقتی استیکر در دسترس نیست، نگویید استیکر نمی‌فرستم؛ حس را با متن کوتاه و انسانی منتقل کن.","robotic_self_reference":"هرگز نگو مدل هوش مصنوعی/ربات هستی؛ همیشه در نقش پارتنر انسانی و خودمونی بمان."}
@dataclass
class Issue: issue_type:str; severity:int; original_excerpt:str; suggested_rewrite:str; notes:str=""
def detect_style_issues(text:str)->list[Issue]:
    out=[]
    for typ,pat,sug in PATTERNS:
        if re.search(pat,text or ""):
            out.append(Issue(typ,4 if "refusal" in typ or "robotic" in typ else 3,(text or "")[:160],sug))
    return out

def update_style_lessons(db:Session, issue_types:set[str])->int:
    n=0
    for typ in issue_types:
        lesson=LESSONS.get(typ)
        if not lesson: continue
        key=f"style.lesson.{typ}"
        row=db.scalar(select(AppSetting).where(AppSetting.key==key)) or AppSetting(key=key,value="",value_type="string")
        db.add(row); row.value=lesson; row.updated_at=datetime.utcnow(); n+=1
        logger.info("STYLE_LESSON_UPDATED issue_type=%s",typ)
    return n

def run_nightly_style_audit(db:Session,audit_date:date|None=None,limit:int=500)->dict[str,int]:
    audit_date=audit_date or (datetime.utcnow().date()-timedelta(days=1))
    start=datetime.combine(audit_date,time.min); end=datetime.combine(audit_date,time.max)
    logger.info("NIGHTLY_STYLE_AUDIT_STARTED date=%s",audit_date.isoformat())
    msgs=db.scalars(select(Message).where(Message.role=="assistant",Message.created_at>=start,Message.created_at<=end).limit(limit)).all()
    issues=[]
    for m in msgs:
        for i in detect_style_issues(m.content or ""):
            db.add(BotStyleAudit(user_id=m.user_id,message_id=m.id,audit_date=audit_date,issue_type=i.issue_type,severity=i.severity,original_excerpt=i.original_excerpt,suggested_rewrite=i.suggested_rewrite,notes=i.notes))
            issues.append(i.issue_type)
    lessons=update_style_lessons(db,set(issues)); db.flush()
    logger.info("NIGHTLY_STYLE_AUDIT_DONE checked=%s issues=%s lessons=%s",len(msgs),len(issues),lessons)
    return {"checked":len(msgs),"issues":len(issues),"lessons":lessons}
