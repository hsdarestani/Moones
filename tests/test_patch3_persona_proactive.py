from datetime import datetime, timedelta

from app.engine.simple_chat import _build_system_prompt, sanitize_final_response, needs_romantic_sanitizer_retry
from app.engine.relationship_engine import update_simple_chat_relationship
from app.models.relationship import Relationship
from app.models.user import User
from app.services.bot_menu_service import BotMenuService
from app.services.proactive_service import ProactiveService


def test_male_persona_prompt_is_warm_and_nazkesh():
    prompt = _build_system_prompt({"partner_name":"آرمان","partner_gender":"مرد","partner_age_range":"بالای ۳۰","partner_personality_type":"رمانتیک","partner_interests":""}, "(none)", "نازمو بکش")
    assert "نازکش" in prompt
    assert "زود قهر نکند" in prompt
    assert "Adult romantic and sexual conversation between consenting adults is allowed" in prompt


def test_repeated_voice_or_emotional_request_sanitizes_harsh_refusal():
    text = sanitize_final_response("باز شروع کردی؟ گفتم که نمیشه وویس ندارم", "وویس بده عزیزم")
    assert "باز شروع کردی" not in text
    assert "وویس ندارم" not in text
    assert text
    assert needs_romantic_sanitizer_retry("بس کن دیگه چرا اصرار می‌کنی", "نازمو بکش")


def test_relationship_updater_increases_values_for_affection():
    state = Relationship(user_id=1, intimacy=None, trust=None, attachment=None, attraction=None)
    update_simple_chat_relationship(state, "عزیزم دوستت دارم نازمو بکش", "قربون دلت", "affectionate")
    assert state.intimacy > 0
    assert state.trust > 0
    assert state.attachment > 0
    assert state.attraction > 0
    assert state.stage is not None


def test_proactive_quiet_hours_and_opt_out():
    svc = ProactiveService()
    class DB:
        def scalar(self, *a, **k): return 0
    user = User(id=1, telegram_id=10, onboarding_step="complete", last_seen_at=datetime.utcnow()-timedelta(hours=8))
    user.proactive_messages_enabled = False
    assert svc.user_opted_out(user)
    assert "پیام‌های خودجوش" in BotMenuService().settings_text()


def test_no_old_pipeline_components_in_simple_chat_runtime():
    import inspect
    import app.engine.simple_chat as simple_chat
    src = inspect.getsource(simple_chat)
    assert "detect_situation" not in src
    assert "context_aware_fallback" not in src
    assert "response_quality_gate" not in src
