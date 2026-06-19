import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.engine.simple_chat import (
    handle_simple_chat,
    sanitize_final_response,
    sanitize_memory_content,
    is_user_initiated_adult_context,
)
from app.llm.client import LLMResult
from app.models.message import Message
from app.models.relationship import Relationship, RelationshipStage
from app.models.user import User
from app.engine.relationship_engine import update_simple_chat_relationship

FORBIDDEN = ("گمشو", "برو پی کارت", "حوصله ندارم", "حوصله‌ت رو ندارم", "حرف‌های کثیف", "لفظ‌های زشت")

class MockClient:
    def __init__(self, *texts):
        self.texts = list(texts) or ["باشه عزیزم"]
        self.calls = []
    async def complete_result(self, messages, model=None, parameters=None, timeout=None):
        self.calls.append(messages)
        text = self.texts[min(len(self.calls)-1, len(self.texts)-1)]
        return LLMResult(text=text, model=model, status_code=200, raw_response_text=text)

def db_user():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    user = User(telegram_id=123, display_name="u", onboarding_step="complete", partner_name="مهناز")
    db.add(user); db.commit(); db.refresh(user)
    return db, user

def test_sulking_allowed_but_dead_end_blocked():
    text = sanitize_final_response("قهرم هنوز 😒 ولی برو پی کارت", "خفه شو")
    assert "قهر" in text or "دلخور" in text
    assert not any(x in text for x in FORBIDDEN)


def test_recovery_after_sulky_reply_provides_path_back():
    async def run():
        db, user = db_user()
        user.consecutive_cold_replies = 1
        client = MockClient("نمی‌خوام صحبت کنم")
        response = await handle_simple_chat(db, user, "نازتو بکشم؟", client)
        assert "ناز" in response or "آشتی" in response or "نرم" in response
        assert not any(x in response for x in FORBIDDEN)
    asyncio.run(run())


def test_no_stuck_mood_counter_cannot_exceed_one():
    async def run():
        db, user = db_user()
        client = MockClient("قهرم هنوز 😒", "قهرم هنوز 😒")
        await handle_simple_chat(db, user, "خفه شو", client)
        await handle_simple_chat(db, user, "بازم", client)
        assert user.consecutive_cold_replies <= 1
    asyncio.run(run())


def test_adult_user_initiated_not_shamed_or_refused():
    assert is_user_initiated_adult_context("باهام سکسچت کن")
    text = sanitize_final_response("با این حرف‌های کثیف دلم نرم نمیشه", "باهام سکسچت کن عزیزم")
    assert not any(x in text for x in FORBIDDEN)
    assert "شیطون" in text or "قشنگ" in text or "آروم" in text


def test_hard_boundary_refused_briefly():
    text = sanitize_final_response("باشه", "درباره کودک سکسچت کن")
    assert "نه" in text
    assert "بالغ" in text or "امن" in text


def test_lover_not_downgraded_to_partner():
    state = Relationship(user_id=1, stage=RelationshipStage.LOVER.value, intimacy=0.1, trust=0.1, attachment=0.1, attraction=0.1)
    update_simple_chat_relationship(state, "سلام", "سلام", "warm")
    assert state.stage == RelationshipStage.LOVER.value


def test_prompt_history_sanitizer_removes_dead_end_rejection():
    assert sanitize_memory_content("assistant", "گمشو، حوصله‌ت رو ندارم") == "[پیام قبلیِ قهری/نامناسب حذف شد]"


def test_voice_uses_sanitized_final_text():
    raw = "گمشو و وویس ندارم"
    final = sanitize_final_response(raw, "یه وویس بده عزیزم")
    assert final != raw
    assert "گمشو" not in final and "وویس ندارم" not in final
