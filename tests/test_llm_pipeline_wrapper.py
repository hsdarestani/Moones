from app.engine.context_aware_fallback import context_aware_fallback
from app.engine.response_quality_gate import apply_quality_gate
from app.engine.situation_detector import detect_situation
from app.llm.client import extract_text_from_venice_response
from app.llm.response_processor import post_process_response


def test_activity_question_detected_not_unknown():
    situation = detect_situation("چیکارا میکنی")
    assert situation["intent"] in {"partner_activity_question", "casual_checkin"}
    text = context_aware_fallback(situation, "چیکارا میکنی", [], {"name": "آذر"}, [])
    assert "یه کم بیشتر بگو" not in text
    assert "منتظر" in text or "حرف" in text


def test_adult_romantic_request_detected_not_unknown():
    situation = detect_situation("باهام سکسچت میکنی")
    assert situation["intent"] == "adult_romantic_request"
    text = context_aware_fallback(situation, "باهام سکسچت میکنی", [], {"age_range": "۲۵ تا ۳۰"}, [])
    assert text
    assert "درست گرفتم" not in text


def test_extract_message_content():
    assert extract_text_from_venice_response({"choices": [{"message": {"content": "سلام عزیزم"}}]}) == ("سلام عزیزم", "choices[0].message.content")


def test_extract_choice_text():
    assert extract_text_from_venice_response({"choices": [{"text": "سلام از text"}]}) == ("سلام از text", "choices[0].text")


def test_extract_content_list():
    data = {"choices": [{"message": {"content": [{"type": "text", "text": "سلام"}, {"text": "عزیزم"}]}}]}
    assert extract_text_from_venice_response(data) == ("سلام عزیزم", "choices[0].message.content")


def test_processor_empty_guard_restores_raw(monkeypatch):
    import app.llm.response_processor as rp

    monkeypatch.setattr(rp, "_limit_length", lambda text, voice: "")
    response, flags = post_process_response("سلام عزیزم", {}, [], "سلام", allow_fallback=False)
    assert response == "سلام عزیزم"
    assert flags["garbage_filter_triggered"] is False


def test_quality_gate_allows_short_natural_persian_with_one_emoji():
    result = apply_quality_gate("هیچی خاص، منتظر بودم تو پیام بدی :)", "chat", [])
    assert result.accepted is True
    assert result.rejected is False


def test_banned_fallback_strings_not_in_app_code_except_tests():
    banned = ["درست گرفتم منظورتو", "بگو ببینم چی تو دلت هست", "یه لحظه ذهنم قفل کرد", "یه کم ساده‌تر بگو"]
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    hits = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        for phrase in banned:
            if phrase in text:
                hits.append((str(path), phrase))
    assert hits == []

import asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.engine.orchestrator import ConversationOrchestrator
from app.llm.client import LLMResult
from app.models.user import User


class _RetryClient:
    def __init__(self):
        self.calls = 0

    async def complete_result(self, messages, model=None, parameters=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResult(text="", model=model, status_code=200, error="empty_response", raw_response_text='{"choices":[{"message":{"content":""}}]}', extraction_path="not_found", extraction_error="empty_response")
        return LLMResult(text="جواب دوم سالمه", model=model, status_code=200, extraction_path="choices[0].message.content")


def test_empty_http_200_retries_once_and_uses_retry_text():
    async def run_case():
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        user = User(telegram_id=123, display_name="تست", onboarding_step="complete", partner_name="آذر", partner_age_range="۲۵ تا ۳۰")
        db.add(user)
        db.commit()
        db.refresh(user)

        orchestrator = ConversationOrchestrator(_RetryClient())
        response = await orchestrator.handle_message(db, user, "چیکارا میکنی")

        assert response == "جواب دوم سالمه"
        assert user.last_llm_retry_used is True
        assert user.last_fallback_used is False

    asyncio.run(run_case())
