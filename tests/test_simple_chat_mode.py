import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.engine.simple_chat import EMERGENCY_RESPONSE, handle_simple_chat
from app.llm.client import LLMResult
from app.models.message import Message
from app.models.user import User


class _MockClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def complete_result(self, messages, model=None, parameters=None, timeout=None):
        self.calls.append({"messages": messages, "model": model, "parameters": parameters})
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, LLMResult):
            return response
        return LLMResult(text=response, model=model, status_code=200, raw_response_text=response, extraction_path="choices[0].message.content")


def _db_user():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    user = User(telegram_id=123, display_name="تست", onboarding_step="complete", partner_name="مهناز", partner_age_range="بالای ۳۰")
    db.add(user)
    db.commit()
    db.refresh(user)
    return db, user


def test_simple_salutation_uses_llm_and_persian_non_empty():
    async def run_case():
        db, user = _db_user()
        client = _MockClient(["سلام عزیزم، خوش اومدی 💙"])
        response = await handle_simple_chat(db, user, "سلام", client)
        assert response == "سلام عزیزم، خوش اومدی 💙"
        assert client.calls[0]["model"] == "qwen-3-6-plus"
        assert user.last_fallback_used is False
        assert user.last_detected_situation is None
    asyncio.run(run_case())


def test_activity_question_has_no_fallback_or_financial_intent():
    async def run_case():
        db, user = _db_user()
        client = _MockClient(["هیچی عزیزم، منتظر پیام تو بودم :)"])
        response = await handle_simple_chat(db, user, "چیکارا میکنی", client)
        assert response == "هیچی عزیزم، منتظر پیام تو بودم :)"
        assert user.last_detected_situation is None
        assert user.last_fallback_used is False
    asyncio.run(run_case())


def test_going_out_not_marked_financial_stress():
    async def run_case():
        db, user = _db_user()
        client = _MockClient(["خوش بگذره عزیزم، کجا می‌خوای بری؟"])
        response = await handle_simple_chat(db, user, "میخوام برم بیرون", client)
        assert response
        assert user.last_detected_situation is None
    asyncio.run(run_case())


def test_mock_venice_content_is_used_exactly_after_label_cleanup():
    async def run_case():
        db, user = _db_user()
        client = _MockClient(["هیچی عزیزم، منتظر پیام تو بودم :)"])
        response = await handle_simple_chat(db, user, "چیکارا میکنی", client)
        assert response == "هیچی عزیزم، منتظر پیام تو بودم :)"
    asyncio.run(run_case())


def test_empty_content_retries_and_never_shows_reasoning_content():
    async def run_case():
        db, user = _db_user()
        first = LLMResult(text="", model="qwen-3-6-plus", status_code=200, raw_response_text='{"reasoning_content":"thinking..."}', extraction_error="empty_response")
        second = LLMResult(text="جواب نهایی فارسی", model="qwen-3-6-plus", status_code=200, raw_response_text='{}')
        client = _MockClient([first, second])
        response = await handle_simple_chat(db, user, "سلام", client)
        assert response == "جواب نهایی فارسی"
        assert user.last_llm_retry_used is True
        assert "thinking" not in response
        assert len(client.calls) == 2
    asyncio.run(run_case())


def test_empty_retry_uses_emergency_without_saving_empty_assistant():
    async def run_case():
        db, user = _db_user()
        client = _MockClient([LLMResult(text="", status_code=200), LLMResult(text="", status_code=200)])
        response = await handle_simple_chat(db, user, "سلام", client)
        assert response == EMERGENCY_RESPONSE
        assistant_messages = db.query(Message).filter_by(user_id=user.id, role="assistant").all()
        assert assistant_messages == []
    asyncio.run(run_case())


def test_simple_mode_payload_params_disable_thinking():
    async def run_case():
        db, user = _db_user()
        client = _MockClient(["سلام عزیزم"])
        await handle_simple_chat(db, user, "سلام", client)
        params = client.calls[0]["parameters"]
        assert params["max_tokens"] == 350
        assert params["temperature"] == 0.75
    asyncio.run(run_case())
