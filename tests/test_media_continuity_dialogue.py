from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.memory import MemoryItem
from app.services.media_continuity_service import record_media_delivery, format_recent_media_context, repair_media_denial
from app.engine.simple_chat import _build_system_prompt, sanitize_final_response


def db():
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, MemoryItem.__table__])
    return sessionmaker(bind=e)()


def user(s):
    u=User(telegram_id=11, display_name='u', onboarding_step='complete', partner_name='سارا', partner_age_range='24', partner_gender='female')
    s.add(u); s.commit(); return u


def test_recent_image_delivery_context_prevents_denial_and_repairs_critique():
    s=db(); u=user(s)
    record_media_delivery(s, user_id=u.id, media_type='image', request_summary='home late-night lounging photo', generated_summary='home sofa reclining sleepy', telegram_message_id=123)
    ctx=format_recent_media_context(s,u.id)
    assert 'generated image was sent recently' in ctx
    fixed=repair_media_denial('نه دیگه، عکس نمی‌فرستم', 'لم ندادی که این خوب نشد', recent_image=True)
    assert 'نمی‌فرستم' not in fixed
    assert 'دقیق درنیومد' in fixed and 'عکس بهتر' in fixed


def test_recent_voice_delivery_context_prevents_voice_denial():
    s=db(); u=user(s)
    record_media_delivery(s, user_id=u.id, media_type='voice', request_summary='warm reply', generated_summary='voice sent', telegram_message_id=124)
    ctx=format_recent_media_context(s,u.id)
    assert 'voice message was sent recently' in ctx
    fixed=repair_media_denial('نمی‌تونم وویس بفرستم', 'وویست چی شد', recent_voice=True)
    assert 'نمی‌تونم' not in fixed and 'وویس' in fixed


def test_system_prompt_natural_image_phrasing_and_no_make_photo_terms():
    prompt=_build_system_prompt({'partner_name':'سارا','partner_gender':'female','partner_age_range':'24','partner_personality_type':'warm','partner_interests':''}, '', 'یه عکس بده', [], media_continuity_context='')
    assert 'یه عکس می‌گیرم برات' in prompt or 'الان برات یه عکس می‌فرستم' in prompt
    assert 'never say «عکس می‌سازم»' in prompt


def test_sanitizer_replaces_awkward_image_generation_phrasing():
    out=sanitize_final_response('باشه، عکس می‌سازم و عکس درست می‌کنم', 'یه عکس بده')
    assert 'عکس می‌سازم' not in out and 'عکس درست می‌کنم' not in out
    assert 'یه عکس می‌گیرم' in out or 'یه عکس می‌فرستم' in out
