from __future__ import annotations
import asyncio, logging, random, re
from datetime import datetime, timedelta, time
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models.human_delivery import HumanDeliveryJob
from app.models.message import Message
from app.services.output_sanitizer import sanitize_output
from app.services.partner_autonomy_policy import violates_autonomy_policy, safe_autonomous_fallback
from app.services.natural_conversation_governor import NaturalConversationGovernor
from app.services.telegram_service import TelegramService
from app.services.outbound_text_policy import sanitize_user_facing_text

logger=logging.getLogger(__name__)
NO_EXTRA_RE=re.compile(r"ساکت|پیام نده|مزاحم نشو|نپر وسط|stop|don't message|dont message",re.I)
BLOCK_SPLIT_RE=re.compile(r"https?://|پرداخت|پشتیبانی|support|admin|quota|limit|محدودیت|عضو کانال|/start|خطا|error|امن|اورژانس",re.I)
SENT_END=tuple(".؟?!…")

def _ends_question(t:str)->bool: return (t or '').strip().endswith(('؟','?'))

def detect_conversation_rhythm(recent_messages:list[Message])->dict:
    now=datetime.utcnow(); users=[m for m in recent_messages if m.role=='user']
    rapid=[m for m in users if m.created_at and m.created_at>=now-timedelta(seconds=90)]
    avg=sum(len(m.content or '') for m in users)/max(1,len(users))
    last=users[-1].content if users else ''
    return {"rapid_fire_count":len(rapid),"avg_len":avg,"short_opener":len(last)<=12 and any(x in last for x in ('چخبر','چه خبر','سلام','هیچی')),"emotional_intensity":sum(1 for x in ('حالم','غم','عجیب','خسته','دلم','گریه') if x in last),"question_density":sum(1 for m in users if _ends_question(m.content))/max(1,len(users)),"silence_gap_seconds":((now-users[-1].created_at).total_seconds() if users and users[-1].created_at else 0)}

class HumanDeliveryService:
    def __init__(self): self.governor=NaturalConversationGovernor()
    def cannot_split(self,text:str,delivery_type:str|None=None,risk_level:str='safe')->bool:
        return delivery_type in {'voice','sticker_only'} or risk_level!='safe' or len(text or '')<95 or bool(BLOCK_SPLIT_RE.search(text or ''))
    def split_text(self,text:str,max_parts:int=3)->list[str]:
        text=(text or '').strip()
        if self.cannot_split(text): return [text] if text else []
        bits=[b.strip() for b in re.split(r'(?<=[.؟?!…])\s+|\n+',text) if b.strip()]
        if len(bits)<2: return [text]
        target=2 if len(text)<360 or random.random()>0.18 else min(3,max_parts)
        parts=['']*target
        for i,b in enumerate(bits): parts[min(i,target-1)]=(parts[min(i,target-1)]+' '+b).strip()
        parts=[p for p in parts if p]
        if len(parts)>1 and any(p==text for p in parts): return [text]
        return parts[:max_parts]
    def apply_question_guard(self,parts:list[str],recent_question_pressure:bool=False,user_id:int|None=None)->list[str]:
        seen=False; out=[]
        for p in parts:
            if _ends_question(p):
                if seen or recent_question_pressure:
                    p=p.rstrip('؟?').strip()+'.'; logger.info('QUESTION_SPAM_GUARD_APPLIED user_id=%s',user_id)
                seen=True
            out.append(p)
        return out
    def guard_part(self,db:Session,user,text:str,kind:str)->str|None:
        res=sanitize_output(text,getattr(user,'id',None)); out=res.text
        if res.changed: logger.info('HUMAN_DELIVERY_PART_SANITIZED user_id=%s kind=%s',getattr(user,'id',None),kind)
        bad,reason=violates_autonomy_policy(out)
        if bad:
            logger.info('AUTONOMY_GUARD_REWRITE user_id=%s reason=%s',getattr(user,'id',None),reason)
            out=safe_autonomous_fallback(user,None,'')
        plan=self.governor.build_style_plan(user,self.governor.classify_user_move('',[],user),[],{})
        if kind in {'afterthought','interjection'}:
            plan.tone='plain'; plan.allow_poetry=False; plan.allow_romance=False; plan.max_chars=140; plan.max_questions=0
        violation=self.governor.validate_response('',out,plan,[])
        if violation.violated:
            logger.info('NATURAL_STYLE_GUARD_FALLBACK user_id=%s reason=%s',getattr(user,'id',None),violation.reason)
            out=self.governor.deterministic_repair('',out,plan,{}) if kind not in {'afterthought','interjection'} else ''
        bad,_=violates_autonomy_policy(out)
        if bad or not out: logger.info('HUMAN_DELIVERY_PART_SKIPPED user_id=%s kind=%s',getattr(user,'id',None),kind); return None
        return out[:140] if kind in {'afterthought','interjection'} else out
    def pending_count(self,db,user): return db.scalar(select(func.count(HumanDeliveryJob.id)).where(HumanDeliveryJob.user_id==user.id,HumanDeliveryJob.status=='pending')) or 0
    def daily_count(self,db,user,kind):
        start=datetime.combine(datetime.utcnow().date(),time.min)
        return db.scalar(select(func.count(HumanDeliveryJob.id)).where(HumanDeliveryJob.user_id==user.id,HumanDeliveryJob.job_type==kind,HumanDeliveryJob.created_at>=start)) or 0
    def cancel_pending_afterthoughts(self,db,user,reason='user_replied')->int:
        rows=db.scalars(select(HumanDeliveryJob).where(HumanDeliveryJob.user_id==user.id,HumanDeliveryJob.status=='pending',HumanDeliveryJob.job_type=='afterthought')).all(); now=datetime.utcnow()
        for r in rows: r.status='cancelled'; r.cancelled_at=now; logger.info('HUMAN_AFTERTHOUGHT_CANCELLED user_id=%s reason=%s',user.id,reason)
        return len(rows)
    def schedule_job(self,db,user,chat_id:int,job_type:str,text:str,delay_seconds:int,source_message_id:int|None=None,metadata:dict|None=None):
        if self.pending_count(db,user)>=1: return None
        guarded=self.guard_part(db,user,text,job_type)
        if not guarded: return None
        now=datetime.utcnow(); row=HumanDeliveryJob(user_id=user.id,telegram_id=user.telegram_id,chat_id=chat_id,job_type=job_type,text=guarded,status='pending',source_message_id=source_message_id,source_created_at=now,scheduled_at=now+timedelta(seconds=delay_seconds),expires_at=now+timedelta(minutes=20),metadata_json=metadata or {})
        db.add(row); db.flush()
        logger.info('HUMAN_%s_SCHEDULED user_id=%s scheduled_at=%s',job_type.upper(),user.id,row.scheduled_at.isoformat())
        return row
    async def run_due_jobs(self,db:Session,limit:int=20)->int:
        now=datetime.utcnow(); rows=db.scalars(select(HumanDeliveryJob).where(HumanDeliveryJob.status=='pending',HumanDeliveryJob.scheduled_at<=now).order_by(HumanDeliveryJob.scheduled_at.asc()).limit(limit)).all(); sent=0
        svc=TelegramService('chat')
        for j in rows:
            try:
                if j.expires_at and j.expires_at<now: j.status='cancelled'; j.cancelled_at=now; logger.info('HUMAN_DELIVERY_JOB_CANCELLED user_id=%s job_type=%s',j.user_id,j.job_type); continue
                surface = 'afterthought' if j.job_type == 'afterthought' else ('interjection' if j.job_type == 'interjection' else 'chat')
                cleaned, issues = sanitize_user_facing_text(j.text, surface=surface)
                if issues: logger.info('OUTBOUND_TEXT_POLICY_APPLIED user_id=%s surface=%s issues=%s',j.user_id,surface,issues)
                if not cleaned:
                    j.status='cancelled'; j.cancelled_at=now; logger.info('HUMAN_DELIVERY_JOB_CANCELLED user_id=%s job_type=%s reason=outbound_policy',j.user_id,j.job_type); continue
                await svc.send_text(j.chat_id,cleaned); j.status='sent'; j.sent_at=datetime.utcnow(); sent+=1
                logger.info('HUMAN_DELIVERY_JOB_SENT user_id=%s job_type=%s',j.user_id,j.job_type)
                if j.job_type=='interjection': logger.info('HUMAN_INTERJECTION_SENT user_id=%s',j.user_id)
            except Exception as exc:
                j.status='failed'; logger.info('HUMAN_DELIVERY_JOB_FAILED user_id=%s job_type=%s reason=%s',j.user_id,j.job_type,type(exc).__name__)
            db.flush()
        return sent
