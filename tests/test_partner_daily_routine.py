from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.message import Message
from app.services.conversation_time_service import ConversationTimeService
from app.services.partner_routine_service import PartnerRoutineService


def db_user():
    engine=create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Message.__table__, __import__("app.models.partner_life", fromlist=["PartnerDailyRoutine"]).PartnerDailyRoutine.__table__])
    db=sessionmaker(bind=engine)()
    user=User(telegram_id=2,onboarding_step="complete",timezone_name="Asia/Tehran")
    db.add(user); db.commit(); db.refresh(user)
    return db,user

def test_routine_creation_idempotent_and_slots():
    db,user=db_user(); svc=PartnerRoutineService()
    morning=ConversationTimeService().build_context(db,user,utc_now=datetime(2026,7,12,5,0,tzinfo=timezone.utc))
    r1=svc.get_or_create_for_context(db,user,morning); r2=svc.get_or_create_for_context(db,user,morning)
    assert r1.id == r2.id
    assert svc.current_slot(r1,morning)["slot_name"] == "morning"
    late=ConversationTimeService().build_context(db,user,utc_now=datetime(2026,7,12,20,0,tzinfo=timezone.utc))
    assert svc.current_slot(r1,late)["slot_name"] == "late_night"
