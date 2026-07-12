from datetime import datetime, timedelta
from types import SimpleNamespace

from app.engine.delivery_decider import decide_delivery


def _user(**kw):
    data = dict(current_mood="warm", last_voice_at=None, last_sticker_at=datetime.utcnow()-timedelta(minutes=10), consecutive_voice_count=0, consecutive_text_count=10)
    data.update(kw)
    return SimpleNamespace(**data)


def test_explicit_voice_request_deterministically_selects_voice(monkeypatch):
    monkeypatch.setattr("app.engine.delivery_decider.random.random", lambda: 0.999)
    d = decide_delivery(_user(), "لطفا وویس بفرست", "باشه عزیزم")
    assert d.delivery_type == "voice"
    assert d.voice_probability >= 1.0


def test_voice_cooldown_can_still_force_text_fallback(monkeypatch):
    monkeypatch.setattr("app.engine.delivery_decider.random.random", lambda: 0.0)
    d = decide_delivery(_user(last_voice_at=datetime.utcnow()), "لطفا وویس بفرست", "باشه عزیزم")
    assert d.delivery_type != "voice"
    assert "voice_cooldown" in d.reason
