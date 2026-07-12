from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User, Message, AppSetting
from app.services.conversation_time_service import ConversationTimeService
from app.engine.simple_chat import _format_time_context_block


def db_user(tz=None):
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, Message.__table__, AppSetting.__table__])
    db=sessionmaker(bind=e)()
    u=User(telegram_id=1,onboarding_step='complete',timezone_name=tz)
    db.add(u); db.commit(); db.refresh(u)
    return db,u


def test_dayparts_and_persian_prompt_authority():
    svc=ConversationTimeService()
    cases=[(datetime(2026,7,11,21,5,tzinfo=timezone.utc),'night'),(datetime(2026,7,11,23,30,tzinfo=timezone.utc),'late_night'),(datetime(2026,7,12,4,30,tzinfo=timezone.utc),'morning'),(datetime(2026,7,12,9,0,tzinfo=timezone.utc),'noon'),(datetime(2026,7,12,12,30,tzinfo=timezone.utc),'afternoon'),(datetime(2026,7,12,16,30,tzinfo=timezone.utc),'evening'),(datetime(2026,7,12,20,0,tzinfo=timezone.utc),'night')]
    for now, expected in cases:
        db,u=db_user('Asia/Tehran')
        c=svc.build_context(db,u,utc_now=now)
        assert c.daypart == expected
    block=_format_time_context_block(c)
    assert '[Authoritative current local time]' in block
    assert 'Current Persian daypart:' in block
    assert 'override greetings' in block or 'override' in block


def test_timezone_precedence_and_invalid_fallback():
    svc=ConversationTimeService()
    db,u=db_user('America/New_York')
    c=svc.build_context(db,u,utc_now=datetime(2026,3,8,7,30,tzinfo=timezone.utc))
    assert c.timezone_name == 'America/New_York'
    db,u=db_user('Bad/Zone')
    db.add(AppSetting(key='roleplay.default_timezone', value='Europe/Paris')); db.commit()
    c=svc.build_context(db,u,utc_now=datetime(2026,3,8,7,30,tzinfo=timezone.utc))
    assert c.timezone_name == 'Europe/Paris'
