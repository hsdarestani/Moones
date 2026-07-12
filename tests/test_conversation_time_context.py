from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.message import Message
from app.services.conversation_time_service import ConversationTimeService


def db_user(tz="Asia/Tehran"):
    engine=create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Message.__table__])
    db=sessionmaker(bind=engine)()
    user=User(telegram_id=1,onboarding_step="complete",timezone_name=tz)
    db.add(user); db.commit(); db.refresh(user)
    return db,user

def add_msg(db,user,role,at):
    m=Message(user_id=user.id,role=role,content="x",created_at=at)
    db.add(m); db.flush(); return m

def ctx_for_gap(seconds, now=datetime(2026,7,12,8,0,tzinfo=timezone.utc), tz="Asia/Tehran"):
    db,user=db_user(tz)
    add_msg(db,user,"user",(now-timedelta(seconds=seconds)).replace(tzinfo=None))
    db.commit()
    return ConversationTimeService().build_context(db,user,utc_now=now)

def test_first_conversation():
    db,user=db_user()
    c=ConversationTimeService().build_context(db,user,utc_now=datetime(2026,7,12,8,0,tzinfo=timezone.utc))
    assert c.gap_bucket == "first_contact" and c.is_first_conversation

def test_gap_buckets():
    assert ctx_for_gap(30).gap_bucket == "rapid_exchange"
    assert ctx_for_gap(300).gap_bucket == "active_session"
    assert ctx_for_gap(7200).gap_bucket == "brief_pause"
    assert ctx_for_gap(21600).gap_bucket == "same_day_return"
    assert ctx_for_gap(2*86400).gap_bucket == "days_away"
    assert ctx_for_gap(10*86400).gap_bucket == "long_return"

def test_crossing_tehran_midnight_is_overnight():
    now=datetime(2026,7,12,21,0,tzinfo=timezone.utc)  # 00:30 Tehran next day
    db,user=db_user("Asia/Tehran")
    add_msg(db,user,"user",datetime(2026,7,12,18,0))  # 21:30 Tehran previous day
    db.commit()
    c=ConversationTimeService().build_context(db,user,utc_now=now)
    assert c.crossed_local_midnight and c.gap_bucket == "overnight_return"

def test_invalid_timezone_fallback_and_dst_conversion(caplog):
    db,user=db_user("Bad/Zone")
    c=ConversationTimeService().build_context(db,user,utc_now=datetime(2026,3,8,7,30,tzinfo=timezone.utc))
    assert c.timezone_name == "Asia/Tehran"
    assert "TIMEZONE_FALLBACK" in caplog.text
    db,user=db_user("America/New_York")
    c=ConversationTimeService().build_context(db,user,utc_now=datetime(2026,3,8,7,30,tzinfo=timezone.utc))
    assert c.local_hour == 3  # DST jump safe conversion

def test_naive_utc_and_exclude_message_id():
    now=datetime(2026,7,12,8,0,tzinfo=timezone.utc)
    db,user=db_user()
    old=add_msg(db,user,"user",datetime(2026,7,12,7,0))
    source=add_msg(db,user,"user",datetime(2026,7,12,7,59,30))
    db.commit()
    c=ConversationTimeService().build_context(db,user,utc_now=now,exclude_message_id=source.id)
    assert c.seconds_since_previous_user == 3600
    assert c.previous_user_message_at == datetime(2026,7,12,7,0,tzinfo=timezone.utc)
