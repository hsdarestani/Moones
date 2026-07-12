import asyncio, os
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.engine.simple_chat import handle_simple_chat
from app.llm.client import LLMResult
from app.models.user import User
from app.models.message import Message
from app.models.settings import AppSetting
from app.models.memory import MemoryItem
from app.models.relationship import Relationship
from app.services.natural_conversation_governor import NaturalConversationGovernor
from app.services.outbound_text_policy import sanitize_user_facing_text
from app.services.proactive_service import ProactiveService

class MockClient:
    def __init__(self, text): self.text=text; self.calls=[]
    async def complete_result(self, messages, **kw): self.calls.append(messages); return LLMResult(text=self.text,raw_response_text=self.text,model="m",status_code=200)

def db_user():
    engine=create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Message.__table__, AppSetting.__table__, MemoryItem.__table__, Relationship.__table__, __import__("app.models.partner_life", fromlist=["PartnerDailyRoutine", "PartnerLifeEvent"]).PartnerDailyRoutine.__table__, __import__("app.models.partner_life", fromlist=["PartnerDailyRoutine", "PartnerLifeEvent"]).PartnerLifeEvent.__table__])
    db=sessionmaker(bind=engine)()
    user=User(telegram_id=3,onboarding_step="complete",partner_name="مهناز",timezone_name="Asia/Tehran")
    db.add(user); db.commit(); db.refresh(user)
    return db,user

def test_simple_chat_prompt_includes_time_and_no_repeated_greeting(monkeypatch):
    import app.engine.simple_chat as sc
    monkeypatch.setattr(sc, "record_ai_usage_event", lambda *a, **k: None)
    monkeypatch.setattr(sc, "user_has_addon", lambda *a, **k: False)
    monkeypatch.setattr(sc.SubscriptionService, "record_successful_llm_response", lambda *a, **k: None)
    monkeypatch.setattr(sc.SubscriptionService, "active_plan_code", lambda *a, **k: "free")
    async def run():
        db,user=db_user(); now=datetime(2026,7,12,8,0,tzinfo=timezone.utc)
        db.add(Message(user_id=user.id,role="user",content="سلام",created_at=now-timedelta(seconds=30))); db.commit()
        client=MockClient("داشتم چای می‌خوردم، بگو ببینم")
        res=await handle_simple_chat(db,user,"چیکار می‌کردی؟",client,time_context_utc_now=now)
        prompt=client.calls[0][0]["content"]
        assert "[Current local time and conversation rhythm]" in prompt
        assert "Gap bucket: rapid_exchange" in prompt
        assert "[Partner current fictional life]" in prompt
        assert "Do not claim real physical activities" not in prompt
        assert "خوش برگشتی" not in res
        assert user.last_gap_bucket == "rapid_exchange"
    asyncio.run(run())

def test_physical_claim_allowed_and_passive_waiting_rejected(monkeypatch):
    monkeypatch.setenv("NATURAL_STYLE_GUARD_ENABLED","true")
    g=NaturalConversationGovernor(); plan=g.build_style_plan(None,g.classify_user_move("چیکار می‌کردی"),[],{"current_routine_slot":{"activity":"چای درست کردن","location":"خانه"}})
    assert not g.validate_response("چیکار می‌کردی؟","داشتم توی خانه چای درست می‌کردم.",plan,[],{"current_routine_slot":{"activity":"چای درست کردن"}}).violated
    assert g.validate_response("کجا بودی؟","فقط منتظرت بودم.",plan,[],{}).violated

def test_outbound_routine_claim_survives_and_bad_status_replaced(monkeypatch):
    monkeypatch.setenv("OUTBOUND_TEXT_POLICY_ENABLED","true")
    text, issues=sanitize_user_facing_text("داشتم توی کافه موسیقی گوش می‌دادم.",surface="chat",user_text="چیکار می‌کردی",roleplay_context={"current_routine_slot":{"activity":"نشستن در کافه","shareable_detail":"موسیقی آروم بود"}})
    assert issues == [] and "کافه" in text
    text, issues=sanitize_user_facing_text("فکرهام رو مرتب کردم",surface="chat",user_text="چیکار می‌کردی",roleplay_context={"current_routine_slot":{"activity":"چای درست کردن","shareable_detail":"پنجره باز بود"}})
    assert issues and "چای" in text

def test_no_morning_greeting_late_night_and_proactive_window_local():
    db,user=db_user(); svc=ProactiveService()
    assert svc.in_quiet_hours(db, datetime(2026,7,12,20,1,tzinfo=timezone.utc), user) is True  # 23:30 Tehran boundary/evening end
    assert svc.in_quiet_hours(db, datetime(2026,7,12,8,0,tzinfo=timezone.utc), user) is False
