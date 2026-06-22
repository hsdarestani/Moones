from datetime import datetime, timedelta
from types import SimpleNamespace
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.db.base import Base
from app.models import User, Message, MemoryItem, AppSetting
from app.engine.relationship_engine import ensure_relationship
from app.engine.simple_chat import _build_system_prompt, _load_long_term_memories
from app.services.memory_digest import run_daily_memory_digest
from app.services.style_audit import detect_style_issues, run_nightly_style_audit
from app.services.partner_style import active_style_lessons
from app.services.proactive_service import ProactiveService
from app.services.settings_service import SettingsService, DEFAULT_SETTINGS


def db():
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e)
    s=Session(e); SettingsService().seed_defaults(s); s.commit(); return s

def user(s, **kw):
    u=User(telegram_id=kw.pop('telegram_id',123), onboarding_step='complete', last_seen_at=datetime.utcnow()-timedelta(hours=10), **kw)
    s.add(u); s.flush(); return u

def test_prompt_includes_partner_style_dna_and_distinguishes_personality_stage_interest():
    u=SimpleNamespace(id=1, partner_name='نازنین', partner_gender='دختر', partner_personality_type='playful', partner_interests='music', current_mood='warm', affection_score=0, trust_score=0, irritation_score=0, playfulness_score=0)
    rel=ensure_relationship(1,None); rel.stage='LOVER'; rel.intimacy=.9; rel.trust=.8; rel.attachment=.7; rel.attraction=.8
    prompt=_build_system_prompt({'partner_name':'نازنین','partner_gender':'دختر','partner_age_range':'۳۰','partner_personality_type':'playful','partner_interests':'music'}, '(none)', 'سلام', ['کاربر لحن خودمونی دوست دارد'], mood=u, relationship=rel, style_lessons=['رسمی حرف نزن'])
    assert '[Partner Style DNA]' in prompt and '[Relationship Stage Behavior]' in prompt and '[Lexical Flavor]' in prompt
    assert 'صدات تو گوشمه' in prompt or 'ریتم' in prompt
    assert 'LOVER' in prompt and 'intimate' in prompt
    assert '[Relevant memories]' in prompt and 'لحن خودمونی' in prompt
    assert '[Active style lessons]' in prompt
    u.partner_personality_type='calm'; rel.stage='STRANGER'
    prompt2=_build_system_prompt({'partner_name':'نازنین','partner_gender':'دختر','partner_age_range':'۳۰','partner_personality_type':'calm','partner_interests':''}, '(none)', 'سلام', [], mood=u, relationship=rel)
    assert prompt != prompt2 and 'STRANGER' in prompt2 and 'respectful' in prompt2

def test_daily_memory_digest_saves_preference_not_every_message_and_merges():
    s=db(); u=user(s)
    now=datetime.utcnow()
    s.add_all([Message(user_id=u.id, role='user', content='سلام', created_at=now), Message(user_id=u.id, role='user', content='یادت باشه من لحن رسمی را دوست ندارم و جواب خودمونی می‌خوام', created_at=now)])
    stats=run_daily_memory_digest(s, now.date(), u.id); s.commit()
    assert stats['saved']==1
    assert s.query(MemoryItem).count()==1
    s.add(Message(user_id=u.id, role='user', content='من لحن رسمی دوست ندارم، خودمونی حرف بزن', created_at=now))
    stats=run_daily_memory_digest(s, now.date(), u.id); s.commit()
    assert stats['merged']>=1
    assert any('رسمی' in m for m in _load_long_term_memories(s,u.id,8))

def test_empty_day_digest_does_nothing():
    s=db(); u=user(s)
    stats=run_daily_memory_digest(s, datetime.utcnow().date(), u.id)
    assert stats['users']==0 and s.query(MemoryItem).count()==0

def test_style_audit_detects_phrases_and_lessons_included():
    issues=detect_style_issues('دریافت کردم، اگر خواستی نشان بده تا ارسال کنم. نمی‌تونم وویس بفرستم و استیکر نمی‌فرستم')
    types={i.issue_type for i in issues}
    assert {'too_formal','voice_capability_refusal','sticker_limitation_mentioned'} <= types
    s=db(); u=user(s); now=datetime.utcnow()
    s.add(Message(user_id=u.id, role='assistant', content='من یک مدل هوش مصنوعی هستم و نمی‌تونم وویس بفرستم', created_at=now)); s.commit()
    stats=run_nightly_style_audit(s, now.date()); s.commit()
    assert stats['issues']>=2
    assert any('وویس' in l or 'هوش مصنوعی' in l for l in active_style_lessons(s,10))

def test_proactive_defaults_null_false_allowed_plans_and_random_schedule():
    s=db()
    assert DEFAULT_SETTINGS['proactive.enabled'][0]=='true'
    assert 'daily' in DEFAULT_SETTINGS['proactive.allowed_plans'][0]
    u=user(s, proactive_messages_enabled=True)
    svc=ProactiveService(); assert svc.enabled(s) is True
    first=svc.schedule_next_proactive(s,u,datetime.utcnow(),reason='test')
    second=svc.schedule_next_proactive(s,u,datetime.utcnow(),reason='test')
    assert first != second and first > datetime.utcnow()+timedelta(hours=1)
    u.proactive_messages_enabled=False
    assert svc.skip_reason(s,u,datetime.utcnow())=='opt_out'
    s.execute(text('UPDATE users SET proactive_messages_enabled=NULL WHERE id=:id'), {'id':u.id}); s.commit()
    s.execute(text('UPDATE users SET proactive_messages_enabled=1 WHERE proactive_messages_enabled IS NULL')); s.commit(); s.refresh(u)
    assert u.proactive_messages_enabled is True

def test_admin_template_has_sections_and_raw_collapsed_no_secret_literals():
    html=open('app/templates/admin/user_detail.html',encoding='utf-8').read()
    assert 'Partner Style DNA' in html and 'Memory & Proactive Summary' in html
    assert '<details class="admin-card admin-raw-panel"><summary>Raw memory records' in html
    assert 'TELEGRAM_TOKEN' not in html and 'DATABASE_URL' not in html
