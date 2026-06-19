import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.engine.delivery_decider import decide_delivery
from app.engine.simple_chat import handle_simple_chat
from app.llm.client import LLMResult
from app.models.user import User

class MockClient:
    def __init__(self, text="باشه عزیزم"):
        self.calls=[]; self.text=text
    async def complete_result(self, messages, model=None, parameters=None, timeout=None):
        self.calls.append(messages)
        return LLMResult(text=self.text, model=model, status_code=200, raw_response_text=self.text)

def db_user():
    engine=create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session=sessionmaker(bind=engine)
    db=Session(); user=User(telegram_id=999, display_name="u", onboarding_step="complete", partner_name="مهناز")
    db.add(user); db.commit(); db.refresh(user)
    return db,user

def test_rude_message_updates_mood_and_prompt_still_calls_ai():
    async def run():
        db,user=db_user(); client=MockClient("باشه، ولی این لحن خوب نبود.")
        response=await handle_simple_chat(db,user,"خفه شو احمق",client)
        assert response == "باشه، ولی این لحن خوب نبود."
        assert user.current_mood in {"slightly_upset","cold"}
        assert user.irritation_score >= 2
        prompt=client.calls[0][0]["content"]
        assert "current_mood:" in prompt
        assert "irritation_score:" in prompt
        assert len(client.calls)==1
    asyncio.run(run())

def test_affectionate_message_increases_affection():
    async def run():
        db,user=db_user(); client=MockClient("منم دوستت دارم")
        await handle_simple_chat(db,user,"عزیزم دوستت دارم",client)
        assert user.affection_score >= 1
        assert user.trust_score >= 1
        assert user.current_mood == "affectionate"
    asyncio.run(run())

def test_voice_recent_cooldown_zero_probability():
    _,user=db_user(); user.last_voice_at=datetime.utcnow()-timedelta(minutes=2)
    decision=decide_delivery(user,"یه ویس بده","کوتاه")
    assert decision.voice_probability == 0
    assert decision.delivery_type != "voice"

def test_voice_requested_high_probability_without_cooldown():
    _,user=db_user(); user.consecutive_text_count=10
    decision=decide_delivery(user,"لطفا ویس بده","باشه عزیزم")
    assert decision.voice_probability >= 0.7

def test_sticker_cooldown_blocks_sticker():
    _,user=db_user(); user.last_sticker_at=datetime.utcnow()-timedelta(minutes=1); user.consecutive_text_count=10
    decision=decide_delivery(user,"سلام","سلام")
    assert decision.sticker_probability == 0
    assert decision.delivery_type not in {"text_plus_sticker","sticker_only"}

def test_no_sticker_configured_skips_silently(monkeypatch):
    from app.core.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("STICKER_CATALOG_JSON", "")
    _,user=db_user(); user.consecutive_text_count=10; user.current_mood="playful"
    decision=decide_delivery(user,"شوخی کنیم","باشه")
    assert decision.sticker_file_id is None
    assert decision.delivery_type not in {"text_plus_sticker","sticker_only"}
    get_settings.cache_clear()

def test_simple_chat_does_not_call_old_pipeline(monkeypatch):
    import app.engine.situation_detector as situation_detector
    import app.engine.context_aware_fallback as context_aware_fallback
    import app.engine.response_quality_gate as response_quality_gate
    monkeypatch.setattr(situation_detector, "detect_situation", lambda *a, **k: (_ for _ in ()).throw(AssertionError("situation called")), raising=False)
    monkeypatch.setattr(context_aware_fallback, "generate_context_aware_fallback", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fallback called")), raising=False)
    monkeypatch.setattr(response_quality_gate, "evaluate_response_quality", lambda *a, **k: (_ for _ in ()).throw(AssertionError("quality called")), raising=False)
    async def run():
        db,user=db_user(); client=MockClient("متن خود AI")
        assert await handle_simple_chat(db,user,"سلام",client) == "متن خود AI"
    asyncio.run(run())

def test_ai_success_used_exactly_no_fallback_replacement():
    async def run():
        db,user=db_user(); client=MockClient("همین جواب نهایی")
        response=await handle_simple_chat(db,user,"هر چی",client)
        assert response == "همین جواب نهایی"
        assert user.last_fallback_used is False
    asyncio.run(run())


def test_venice_thinking_only_retries_and_hides_reasoning(monkeypatch):
    import httpx
    from app.core.config import get_settings
    from app.llm.client import LLMClient
    get_settings.cache_clear()
    monkeypatch.setenv("VENICE_API_KEY", "test-key")
    calls=[]
    class FakeResponse:
        status_code=200
        headers={}
        def __init__(self, payload):
            self._payload=payload; self.text=str(payload)
        def json(self): return self._payload
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            calls.append(json)
            if len(calls)==1:
                return FakeResponse({"choices":[{"message":{"content":"", "reasoning_content":"secret thoughts"}}]})
            return FakeResponse({"choices":[{"message":{"content":"جواب نهایی"}}]})
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    async def run():
        result=await LLMClient().complete_result([{"role":"user","content":"سلام"}], model="qwen-3-6-plus")
        assert result.text == "جواب نهایی"
        assert result.retry_used is True
        assert "secret thoughts" not in result.text
        assert len(calls) == 2
        assert calls[0]["venice_parameters"]["disable_thinking"] is True
        assert calls[1]["venice_parameters"]["disable_thinking"] is True
    asyncio.run(run())
    get_settings.cache_clear()


def test_memory_sanitizer_replaces_old_voice_and_sticker_denials():
    from app.engine.simple_chat import sanitize_memory_content
    assert sanitize_memory_content("assistant", "نمی‌تونم وویس بدم عزیزم") == "[پیام قبلیِ قهری/نامناسب حذف شد]"
    assert sanitize_memory_content("assistant", "استیکر ندارم") == "[پیام قبلیِ قهری/نامناسب حذف شد]"


def test_voice_and_sticker_final_sanitizer_removes_bad_phrases():
    from app.engine.simple_chat import sanitize_final_response
    voice = sanitize_final_response("نمی‌تونم وویس بدم فقط متنی", "ویس بده")
    sticker = sanitize_final_response("استیکر ندارم چرا اصرار می‌کنی", "استیکر بده")
    assert "وویس ندارم" not in voice and "فقط متنی" not in voice and "نمی‌تونم" not in voice
    assert "استیکر ندارم" not in sticker and "اصرار" not in sticker


def test_explicit_sticker_bypasses_consecutive_text_cooldown(monkeypatch):
    from app.models.sticker import StickerItem
    db,user=db_user(); user.consecutive_text_count=0
    db.add(StickerItem(telegram_file_id="sticker-file", label="warm", usage_context="warm", is_active=True, weight=1))
    db.commit()
    decision=decide_delivery(user,"یه استیکر بده","باشه",db)
    assert decision.delivery_type == "sticker_only"
    assert decision.sticker_file_id == "sticker-file"


def test_mark_delivery_keyword_is_sticker_sent():
    import inspect
    from app.engine.delivery_decider import mark_delivery
    assert "sticker_sent" in inspect.signature(mark_delivery).parameters
    assert "sticker_used" not in inspect.signature(mark_delivery).parameters
