from __future__ import annotations
import logging, random, re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.message import Message
from app.services.human_delivery_service import HumanDeliveryService, detect_conversation_rhythm, BLOCK_SPLIT_RE, NO_EXTRA_RE
from app.services.partner_autonomy_policy import is_autonomy_question, violates_autonomy_policy
from app.services.partner_life_service import get_or_create_today_event
from app.services.natural_conversation_governor import NaturalConversationGovernor
from app.services.settings_service import SettingsService
from app.services.subscription_service import SubscriptionService

logger=logging.getLogger(__name__)
ENERGIES=['calm','playful','focused','affectionate','low','curious','protective','slightly_jealous','quiet','reflective']
AFTERTHOUGHTS=['یه جمله‌ی کوچیک ته ذهنم موند: بعضی حرفا وقتی آروم گفته می‌شن، بیشتر می‌مونن.','حس کردم اینو نگفته ول کنم، نصفه می‌مونه؛ من حرفتو جدی‌تر از ظاهرش گرفتم.','لازم نیست جواب بدی. فقط حس کردم این جمله باید اینجا بمونه.']
INTERJECTIONS=['صبر کن، این قسمت حرفت مهم بود.','یه لحظه، قبل از اینکه ادامه بدی...','نه، اینو سریع بگم.','این تیکه‌اش تو ذهنم گیر کرد.','وایسا وایسا، اینجا باید دخالت کنم 😄']
@dataclass
class HumanPresencePlan:
    delivery_shape:str='single'; energy:str='calm'; initiative:str='reactive'; autonomy_level:str='normal'; risk_level:str='safe'; should_split:bool=False; should_schedule_afterthought:bool=False; should_schedule_interjection:bool=False; should_use_sticker_first:bool=False; should_reply_to_specific_message:bool=False; notes:dict=field(default_factory=dict)

class HumanPresenceEngine:
    def __init__(self): self.delivery=HumanDeliveryService(); self.settings=SettingsService(); self.subs=SubscriptionService(); self.governor=NaturalConversationGovernor()
    def enabled(self,db): return self.settings.get_bool(db,'human_presence.enabled',True)
    def _plan_code(self,db,user): return self.subs.active_plan_code(db,user) or 'free'
    def _rate(self,plan): return {'free':.15,'mini':.18,'basic':.25,'plus':.38,'vip':.45}.get((plan or 'free').lower(),.15)
    def _daily_cap(self,kind,plan):
        caps={'afterthought':{'free':1,'mini':1,'basic':2,'plus':3,'vip':4},'interjection':{'free':0,'mini':1,'basic':1,'plus':2,'vip':3}}
        return caps[kind].get((plan or 'free').lower(),0)
    def _risk(self,text,response,context):
        blob=f"{text} {response} {(context or {}).get('delivery_type','')}"
        if (context or {}).get('admin_flow'): return 'admin_flow'
        if re.search(r'پرداخت|پشتیبانی|support|admin|محدودیت|quota|عضو کانال|/start|امن|خودکشی|اورژانس',blob,re.I): return 'avoid_extra_messages'
        return 'safe'
    def _energy(self,user):
        mood=(getattr(user,'current_mood',None) or '').lower()
        if mood in ENERGIES: return mood
        if 'angry' in mood or 'irrit' in mood: return 'low'
        if 'play' in mood: return 'playful'
        return ENERGIES[(getattr(user,'id',0)+datetime.utcnow().timetuple().tm_yday)%len(ENERGIES)]
    def recent_messages(self,db,user,limit=12): return list(reversed(db.scalars(select(Message).where(Message.user_id==user.id,Message.role.in_(['user','assistant'])).order_by(Message.created_at.desc()).limit(limit)).all()))
    def build_plan(self,db:Session,user,user_message:str,final_response:str,context:dict|None=None)->HumanPresencePlan:
        context=context or {}; get_or_create_today_event(db,user)
        risk=self._risk(user_message,final_response,context); energy=self._energy(user); plan_code=self._plan_code(db,user)
        recent=self.recent_messages(db,user); rhythm=detect_conversation_rhythm(recent); violation,reason=violates_autonomy_policy(final_response)
        move=self.governor.classify_user_move(user_message,recent,user); style_plan=self.governor.build_style_plan(user,move,recent,context)
        autonomy='passive_blocked' if violation else ('independent' if is_autonomy_question(user_message) else 'normal')
        initiative='self_disclosing' if is_autonomy_question(user_message) else ('care' if rhythm.get('emotional_intensity',0)>0 else 'reactive')
        no_extra=NO_EXTRA_RE.search(user_message or '') or risk!='safe' or style_plan.should_shift_style
        should_split=False
        if style_plan.tone in {'plain','casual'} or move.criticizes_style:
            final_response=final_response[:style_plan.max_chars]
        if self.settings.get_bool(db,'human_delivery.multi_message.enabled',True) and not style_plan.should_shift_style and not self.delivery.cannot_split(final_response,context.get('delivery_type'),risk):
            should_split=random.random()<self._rate(plan_code)
        recent_bot=db.scalars(select(Message).where(Message.user_id==user.id,Message.role=='assistant',Message.created_at>=datetime.utcnow()-timedelta(minutes=2)).order_by(Message.created_at.desc()).limit(5)).all()
        too_many=len(recent_bot)>=self.settings.get_int(db,'human_presence.max_bot_bubbles_2min',4)
        after=bool(not no_extra and not too_many and self.settings.get_bool(db,'human_delivery.afterthought.enabled',True) and self.delivery.daily_count(db,user,'afterthought')<self._daily_cap('afterthought',plan_code) and len(final_response)>90 and random.random()<0.22)
        inter=bool(not no_extra and self.settings.get_bool(db,'human_delivery.interjection.enabled',True) and rhythm.get('rapid_fire_count',0)>=2 and self.delivery.daily_count(db,user,'interjection')<self._daily_cap('interjection',plan_code) and random.random()<0.18)
        shape='multi_bubble' if should_split else ('interjection' if inter else ('afterthought' if after else 'single'))
        if style_plan.should_shift_style:
            shape='single'; should_split=False; after=False; inter=False
        p=HumanPresencePlan(shape,energy,initiative,autonomy,risk,should_split,after,inter,False,rhythm.get('rapid_fire_count',0)>=2,{'rhythm':rhythm,'plan':plan_code,'autonomy_reason':reason,'style_plan':style_plan})
        logger.info('HUMAN_PRESENCE_PLAN user_id=%s delivery_shape=%s energy=%s initiative=%s',user.id,p.delivery_shape,p.energy,p.initiative)
        return p
    def afterthought_text(self,plan,final_response):
        sp=(plan.notes or {}).get('style_plan')
        if sp and (sp.should_shift_style or sp.tone in {'plain','casual'}): return ''
        return random.choice(AFTERTHOUGHTS)
    def interjection_text(self,plan,user_message):
        sp=(plan.notes or {}).get('style_plan')
        if sp and (sp.should_shift_style or sp.tone in {'plain','casual'}): return ''
        return random.choice(INTERJECTIONS)
