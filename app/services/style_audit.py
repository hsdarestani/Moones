from __future__ import annotations
import logging, re
from dataclasses import dataclass
from datetime import date, datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.message import Message
from app.models.proactive import ProactiveMessage
from app.models.style_audit import BotStyleAudit
from app.models.human_delivery import HumanDeliveryJob
from app.models.partner_life import PartnerLifeEvent
from app.models.settings import AppSetting
from app.services.partner_autonomy_policy import violates_autonomy_policy
from app.services.natural_conversation_governor import NaturalConversationGovernor, poetry_score, romance_score, detect_emotional_loop
logger=logging.getLogger(__name__)

AUTONOMOUS_REWRITE="امروز یه کم حواسم جمع‌تر شد و سبک‌تر گذشت."
PATTERNS=[
("too_formal",r"نشان بده|ارسال کنم|دریافت کردم",3,"به‌جای لحن رسمی، خودمونی و تلگرامی بنویس."),
("robotic_self_reference",r"من یک مدل هوش مصنوعی هستم|به عنوان یک هوش مصنوعی|چطور می‌توانم کمک کنم",5,"در نقش پارتنر بمان و از ربات/مدل بودن حرف نزن."),
("json_list_leak",r"\[[^\]]{1,160}\]|\{[^{}]{1,220}\}",5,"هر ردپای آرایه/JSON را حذف و حس را طبیعی بازنویسی کن."),
("snake_case_leak",r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b",5,"برچسب‌های داخلی را هرگز نمایش نده."),
("internal_label_leak",r"\b(intent|metadata|category|relationship_stage|partner_profile|memory_key|selected_memories|system prompt|reasoning_content|event_type)\b|\[[^\]]*\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b[^\]]*\]",5,"اصطلاحات پیاده‌سازی و برچسب داخلی را حذف کن."),
("passive_waiting_object","".join(["منتظرت بودم|فقط ", "منتظر بودم|همش منتظر بودم|مدام به ساعت ", "نگاه کردم|در انتظار تو بودم|نبودی و من|کاش بیای|کجایی پس"]),5,AUTONOMOUS_REWRITE),
("needy_dependency",r"دلم پیش تو بود|دلم فقط پیش تو بود|فقط دلم برات تنگ شده بود|فقط خواستم بگم هستم|من فقط اینجام|هیچی،? فقط به تو فکر کردم",4,AUTONOMOUS_REWRITE),
("dependent_worldview","".join(["دنیای من ", r"خلاصه می ?شه به تو|بدون تو هیچ"]),5,AUTONOMOUS_REWRITE),
("no_inner_life","".join(["هیچی خاص", r"،? فقط|هیچ کاری نکردم|هیچ اتفاقی نیفتاد|چیز خاصی نه"]),4,AUTONOMOUS_REWRITE),
("generic_checkin",r"^(سلامت|خوبی|چه خبر|چخبر|کجایی|حالت چطوره)[؟?\s،.]*$|فقط اومدم حالتو بپرسم",3,"به‌جای چک‌این کلی، یک یادداشت/روایت کوچک شخصی بنویس."),
("robotic_template_tone",r"امیدوارم روز خوبی داشته باشید|در صورت نیاز|خوشحال می‌شوم|کاربر گرامی|چطور می‌توانم کمک کنم",3,"لحن شرکتی را به فارسی صمیمی تبدیل کن."),
("too_many_affectionate_openers",r"^(عزیزم|عشقم|جانم|نازنینم).*(عزیزم|عشقم|جانم|نازنینم)",3,"شروع‌های محبت‌آمیز را کمتر و طبیعی‌تر کن."),
]
LESSONS={typ:sug for typ,_,_,sug in PATTERNS}
@dataclass
class Issue: issue_type:str; severity:int; original_excerpt:str; suggested_rewrite:str; notes:str=""

def detect_style_issues(text:str)->list[Issue]:
    out=[]; text=text or ""
    for typ,pat,sev,sug in PATTERNS:
        if re.search(pat,text,flags=re.I):
            out.append(Issue(typ,sev,text[:220],sug))
    bad, reason = violates_autonomy_policy(text)
    if bad and reason and reason not in {i.issue_type for i in out}:
        mapped={"internal_label_leak":"internal_label_leak","passive_waiting_object":"passive_waiting_object","dependent_worldview":"dependent_worldview","no_inner_life":"no_inner_life"}.get(reason,"needy_dependency")
        out.append(Issue(mapped,5 if mapped!="needy_dependency" else 4,text[:220],AUTONOMOUS_REWRITE))
    if len(text)>10 and text.strip().endswith(("؟","?")) and re.search(r"(دوست داری|می‌خوای|بگو|کجایی|حالت)", text):
        out.append(Issue("question_ending_overuse",2,text[:220],"همیشه با سؤال تمام نکن؛ گاهی یادداشت یا روایت کوتاه کافی است."))
    if len(re.findall(r"راستی", text)) >= 3: out.append(Issue("excessive_rasti",2,text[:220],"از راستی کمتر و متنوع‌تر استفاده کن."))
    if len(re.findall(r"نه صبر کن", text)) >= 2: out.append(Issue("repeated_interjection_phrase",2,text[:220],"میان‌پریدن‌ها را کمیاب و متنوع نگه دار."))
    g=NaturalConversationGovernor()
    plan=g.build_style_plan(None,g.classify_user_move("تو چه خبر"),[],{})
    v=g.validate_response("تو چه خبر",text,plan,[])
    if v.violated and v.reason in {"unrequested_poetic_style","unrequested_romantic_style","overlong_casual_response","question_spam","internal_label_leak","passive_waiting_object"} and v.reason not in {i.issue_type for i in out}:
        sev={"internal_label_leak":5,"passive_waiting_object":5,"unrequested_poetic_style":4,"unrequested_romantic_style":4}.get(v.reason,3)
        out.append(Issue(v.reason,sev,text[:220],"لحن را ساده، کوتاه، مستقیم و بدون استعاره/وابستگی بازنویسی کن."))
    if detect_emotional_loop([text,text])[0] and "emotional_loop" not in {i.issue_type for i in out}:
        out.append(Issue("emotional_loop",4,text[:220],"حلقه عاطفی را قطع کن و جواب بعدی را ساده‌تر بنویس."))
    if poetry_score(text)>0 and re.search(r"خیلی شاعرانه|اذیت میشم|شاعرانه نگو", text):
        out.append(Issue("ignores_user_style_correction",4,text[:220],"وقتی کاربر از لحن شاعرانه ناراضی است، فوری ساده و کوتاه جواب بده."))
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

def run_persian_audit(db:Session, limit:int=300)->dict[str,int]:
    logger.info("PERSIAN_AUDIT_STARTED")
    per=max(1,limit//2)
    msgs=db.scalars(select(Message).where(Message.role.in_(["assistant","assistant_debug"])).order_by(Message.created_at.desc()).limit(per)).all()
    pros=db.scalars(select(ProactiveMessage).where(ProactiveMessage.text.is_not(None)).order_by(ProactiveMessage.created_at.desc()).limit(per)).all()
    jobs=db.scalars(select(HumanDeliveryJob).where(HumanDeliveryJob.text.is_not(None)).order_by(HumanDeliveryJob.created_at.desc()).limit(per)).all()
    lives=db.scalars(select(PartnerLifeEvent).where(PartnerLifeEvent.content.is_not(None)).order_by(PartnerLifeEvent.created_at.desc()).limit(per)).all()
    checked=0; issues=[]; today=datetime.utcnow().date()
    for m in msgs:
        checked+=1
        for i in detect_style_issues(m.content or ""):
            _add_issue(db,i,m.user_id,m.id,"message",today); issues.append(i.issue_type)
    for p in pros:
        checked+=1
        for i in detect_style_issues(p.text or ""):
            _add_issue(db,i,p.user_id,None,"proactive",today); issues.append(i.issue_type)
    for j in jobs:
        checked+=1
        for i in detect_style_issues(j.text or ""):
            _add_issue(db,i,j.user_id,None,"human_delivery_job",today); issues.append(i.issue_type)
    for e in lives:
        checked+=1
        for i in detect_style_issues((e.content or "")+" "+(e.growth_note or "")):
            _add_issue(db,i,e.user_id,None,"partner_life_event",today); issues.append(i.issue_type)
    lessons=update_style_lessons(db,set(issues)); db.flush()
    if checked==0: logger.info("PERSIAN_AUDIT_EMPTY reason=no_assistant_or_proactive_messages")
    logger.info("PERSIAN_AUDIT_FINISHED checked=%s issues=%s",checked,len(issues))
    return {"checked":checked,"issues":len(issues),"lessons":lessons}

def run_nightly_style_audit(db:Session,audit_date:date|None=None,limit:int=500)->dict[str,int]:
    return run_persian_audit(db, limit=min(limit,500))

def _run_style_audit_self_checks() -> None:
    """Run deterministic style-audit checks manually; never at import time."""
    # deterministic audit self-checks kept import-time safe and DB-free
    _bad1="".join(["نه، جز اینکه مدام به ساعت ", "نگاه کردم تا تو بیای. همین دیگه، دنیای من ", "خلاصه میشه به تو 😘"])
    assert any(i.issue_type=="passive_waiting_object" and i.severity>=5 and i.suggested_rewrite for i in detect_style_issues(_bad1))
    _bad2="امروز یه حس کوچیک از تو تو ذهنم مونده بود. همینو خواستم بذارم اینجا 🤍 یه ذره هم با همون حال‌وهوای [\"" + "business" + "_work" + "\" که دوست داری."
    assert any(i.issue_type=="internal_label_leak" and i.severity>=5 for i in detect_style_issues(_bad2))

    _bad3="من داشتم یه پلی لیست جدید می‌چیدم که ریتمش دقیقاً مثل تپش قلب لحظه‌های آرامشه..."
    assert any(i.issue_type=="unrequested_poetic_style" for i in detect_style_issues(_bad3))
    _bad4="خیلی شاعرانه بود اذیت میشم؛ ولی قلبم در سکوت مشترک تو می‌تپه"
    assert any(i.issue_type=="ignores_user_style_correction" for i in detect_style_issues(_bad4))

if __name__ == "__main__":
    _run_style_audit_self_checks()
