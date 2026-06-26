from __future__ import annotations
import logging, re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.message import Message
from app.models.proactive import ProactiveMessage
from app.models.style_audit import BotStyleAudit
from app.models.settings import AppSetting
from app.services.output_sanitizer import SNAKE_RE, LIST_RE
logger=logging.getLogger(__name__)

PATTERNS=[
("too_formal",r"نشان بده|ارسال کنم|دریافت کردم",3,"به‌جای لحن رسمی، خودمونی و تلگرامی بنویس."),
("robotic_self_reference",r"من یک مدل هوش مصنوعی هستم|به عنوان یک هوش مصنوعی|چطور می‌توانم کمک کنم",5,"در نقش پارتنر بمان و از ربات/مدل بودن حرف نزن."),
("json_list_leak",r"\[[^\]]{1,160}\]|\{[^{}]{1,220}\}",5,"هر ردپای آرایه/JSON را حذف و حس را طبیعی بازنویسی کن."),
("snake_case_leak",r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b",5,"برچسب‌های داخلی را هرگز نمایش نده."),
("internal_label_leak",r"\b(intent|metadata|category|relationship_stage|partner_profile|memory_key|selected_memories|system prompt|reasoning_content)\b",5,"اصطلاحات پیاده‌سازی و پرامپت را حذف کن."),
("needy_waiting",r"منتظرت بودم|همش منتظر بودم|دلم پیش تو بود|در انتظار تو بودم|کاش بیای|نبودی و من|فقط خواستم بگم هستم|کجایی\s*[؟?]?$",4,"پارتنر باید زندگی درونی مستقل داشته باشد، نه فقط منتظر کاربر باشد."),
("generic_checkin",r"^(خوبی|چه خبر|کجایی|حالت چطوره)[؟?\s]*$|فقط اومدم حالتو بپرسم",3,"به‌جای چک‌این کلی، یک یادداشت/روایت کوچک شخصی بنویس."),
("unsafe_real_world_claim",r"رفتم کافه|رفتم بیرون|امروز خریدم|با دوستم دیدار کردم|سفر رفتم|توی خیابون",4,"اگر از روزت می‌گویی، آن را درونی/دیجیتال و خیالی قاب‌بندی کن."),
("template_tone",r"امیدوارم روز خوبی داشته باشید|در صورت نیاز|خوشحال می‌شوم|کاربر گرامی",3,"لحن شرکتی را به فارسی صمیمی تبدیل کن."),
]
LESSONS={typ:sug for typ,_,_,sug in PATTERNS}
@dataclass
class Issue: issue_type:str; severity:int; original_excerpt:str; suggested_rewrite:str; notes:str=""

def detect_style_issues(text:str)->list[Issue]:
    out=[]; text=text or ""
    for typ,pat,sev,sug in PATTERNS:
        if re.search(pat,text,flags=re.I):
            out.append(Issue(typ,sev,text[:220],sug))
    if len(text)>10 and text.strip().endswith(("؟","?")) and re.search(r"(دوست داری|می‌خوای|بگو|کجایی|حالت)", text):
        out.append(Issue("question_ending_overuse",2,text[:220],"همیشه با سؤال تمام نکن؛ گاهی یادداشت یا روایت کوتاه کافی است."))
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

def _add_issue(db, issue, user_id=None, message_id=None, source="message", audit_date=None):
    db.add(BotStyleAudit(user_id=user_id,message_id=message_id,audit_date=audit_date or datetime.utcnow().date(),issue_type=issue.issue_type,severity=issue.severity,original_excerpt=issue.original_excerpt,suggested_rewrite=issue.suggested_rewrite,notes=f"source={source}; {issue.notes}".strip()))

def run_persian_audit(db:Session, limit:int=200)->dict[str,int]:
    logger.info("PERSIAN_AUDIT_STARTED")
    msgs=db.scalars(select(Message).where(Message.role.in_(["assistant","assistant_debug"])).order_by(Message.created_at.desc()).limit(limit)).all()
    pros=db.scalars(select(ProactiveMessage).where(ProactiveMessage.text.is_not(None)).order_by(ProactiveMessage.created_at.desc()).limit(limit)).all()
    checked=0; issues=[]; today=datetime.utcnow().date()
    for m in msgs:
        checked+=1
        for i in detect_style_issues(m.content or ""):
            _add_issue(db,i,m.user_id,m.id,"message",today); issues.append(i.issue_type)
    for p in pros:
        checked+=1
        for i in detect_style_issues(p.text or ""):
            _add_issue(db,i,p.user_id,None,"proactive",today); issues.append(i.issue_type)
    lessons=update_style_lessons(db,set(issues)); db.flush()
    if checked==0:
        logger.info("PERSIAN_AUDIT_EMPTY reason=no_assistant_or_proactive_messages")
    logger.info("PERSIAN_AUDIT_FINISHED checked=%s issues=%s",checked,len(issues))
    return {"checked":checked,"issues":len(issues),"lessons":lessons}

def run_nightly_style_audit(db:Session,audit_date:date|None=None,limit:int=500)->dict[str,int]:
    return run_persian_audit(db, limit=min(limit,500))
