import asyncio
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.engine.simple_chat import handle_simple_chat
from app.llm.client import LLMResult
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from app.models import User, Message, Wallet, WalletTransaction, UsageCharge, AppSetting, AiUsageEvent, PartnerDailyRoutine, PartnerLifeEvent, Relationship, MemoryItem, BotStyleAudit, MediaMessage

@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


class MockClient:
    def __init__(self, responses):
        self.responses=responses; self.calls=[]
    async def complete_result(self, messages, model=None, parameters=None, timeout=None):
        self.calls.append(messages[0]['content'])
        text=self.responses[min(len(self.calls)-1, len(self.responses)-1)]
        return text if isinstance(text, LLMResult) else LLMResult(text=text, model=model, status_code=200, raw_response_text=text)


def db_user():
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e)
    db=sessionmaker(bind=e)()
    u=User(telegram_id=55,onboarding_step='complete',partner_name='مهناز',partner_age_range='بالای ۳۰',timezone_name='Asia/Tehran')
    db.add(u); db.flush(); db.add(Wallet(user_id=u.id,balance_coins=10000)); db.commit(); db.refresh(u)
    return db,u


def test_conflicting_user_claim_adds_prompt_block_and_repairs_when_guard_disabled(monkeypatch):
    monkeypatch.setenv('NATURAL_STYLE_GUARD_ENABLED','false')
    async def run():
        db,u=db_user(); c=MockClient(['سلام صبح بخیر'])
        res=await handle_simple_chat(db,u,'صبح بخیر',c,time_context_utc_now=datetime(2026,7,11,21,5,tzinfo=timezone.utc))
        assert 'Temporal correction needed' in c.calls[0]
        assert 'صبح بخیر' not in res
        assert db.query(UsageCharge).count() == 1
    asyncio.run(run())


def test_temporal_retry_maximum_one_for_complex_contradiction(monkeypatch):
    monkeypatch.setenv('NATURAL_STYLE_GUARD_ENABLED','false')
    async def run():
        db,u=db_user(); c=MockClient(['الان سر ظهره', 'بازم سر ظهره'])
        res=await handle_simple_chat(db,u,'سر ظهره الان',c,time_context_utc_now=datetime(2026,7,11,21,5,tzinfo=timezone.utc))
        assert len(c.calls) <= 2
        assert 'سر ظهره' not in res
        assert 'سرور' not in res and 'سیستم' not in res and 'پرامپت' not in res
    asyncio.run(run())


def test_time_question_uses_authoritative_context_in_prompt():
    async def run():
        db,u=db_user(); c=MockClient(['الان حدود دوازده و نیم شبه.'])
        res=await handle_simple_chat(db,u,'ساعت چنده؟',c,time_context_utc_now=datetime(2026,7,11,21,0,tzinfo=timezone.utc))
        assert 'Current local clock: 00:30' in c.calls[0]
        assert 'دوازده و نیم' in res
    asyncio.run(run())
