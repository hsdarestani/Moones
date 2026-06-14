from app.engine.persona_voice_engine import generate_voice_profile
from app.llm.prompt_builder import build_prompt
from app.llm.response_processor import is_garbage_output, post_process_response
from app.engine.emotion_engine import Emotion
from app.engine.policy_engine import ResponsePolicy
from app.models.relationship import Relationship


def _policy() -> ResponsePolicy:
    return ResponsePolicy(tone="warm", depth=0.4, flirt_level=0.2, memory_usage=0.3)


def test_playful_18_20_voice_short_and_informal_without_repeated_suffix():
    voice = generate_voice_profile(
        {"name": "رها", "gender": "دختر", "age_range": "۱۸ تا ۲۰", "personality_type": "شوخ و بازیگوش", "interests": ["شوخی و خنده"]},
        {"stage": "FAMILIAR", "intimacy": 0.2, "trust": 0.2, "attachment": 0.1},
        [],
        "حرف بزن باهام",
    )
    processed, flags = post_process_response("باشه، ولی اول تو بگو امروز چرا اینقدر ساکتی؟ 😄\n\nمن اینجام، آروم برام بگو", voice, [], "حرف بزن باهام")
    assert voice["sentence_length"] == "short"
    assert voice["playfulness"] > 0.7
    assert "در خدمتم" not in processed
    assert "من اینجام، آروم برام بگو" not in processed
    assert len(processed) < 190
    assert flags == {"garbage_filter_triggered": False, "repetition_filter_triggered": False}


def test_deep_26_30_emotional_user_gets_low_emoji_thoughtful_voice():
    voice = generate_voice_profile(
        {"name": "مانی", "gender": "پسر", "age_range": "۲۶ تا ۳۰", "personality_type": "عمیق و اهل فکر", "interests": ["حرف‌های عمیق"]},
        {"stage": "FRIEND", "intimacy": 0.4, "trust": 0.5, "attachment": 0.3},
        ["کاربر جواب‌های عمیق و آرام را دوست دارد"],
        "حالم خوب نیست",
    )
    processed, _ = post_process_response("می‌فهمم… لازم نیست الان همه‌چیز رو توضیح بدی. فقط از همین لحظه بگو سنگینیِ حالت بیشتر از کجاست؟ 🫶", voice, [], "حالم خوب نیست")
    assert voice["depth"] > 0.75
    assert voice["emoji_probability"] < 0.12
    assert "🫶" not in processed
    assert "کلیشه" not in processed


def test_romantic_21_25_warm_but_not_dramatic():
    voice = generate_voice_profile(
        {"name": "نیکا", "gender": "دختر", "age_range": "۲۱ تا ۲۵", "personality_type": "رمانتیک و احساسی", "interests": ["موسیقی"]},
        {"stage": "ROMANTIC", "intimacy": 0.6, "trust": 0.5, "attachment": 0.5},
        [],
        "دلم گرفته",
    )
    processed, _ = post_process_response("بیا امشب آروم‌تر حرف بزنیم؛ لازم نیست قوی بازی دربیاری، من با همین حالتم کنارت می‌مونم.", voice, [], "دلم گرفته")
    assert voice["romance"] > 0.8
    assert "می‌میرم" not in processed
    assert len(processed) < 360


def test_where_are_you_prompt_does_not_invent_exact_neighborhood():
    state = Relationship(stage="STRANGER", intimacy=0.05, trust=0.05, attachment=0.05, attraction=0.03, dependency=0.0)
    profile = {"name": "آوا", "gender": "دختر", "age_range": "۲۱ تا ۲۵", "personality_type": "آروم و مهربون", "interests": ["سفر و طبیعت‌گردی"]}
    voice = generate_voice_profile(profile, state, [], "کجایی هستی؟")
    prompt = build_prompt("کجایی هستی؟", state, Emotion.NEUTRAL, _policy(), [], profile, [], voice)
    system = prompt[0]["content"]
    assert "Do not invent a city" in system
    assert "no exact city unless memory/profile contains one" in system
    assert "تهران" not in system


def test_garbage_output_gets_safe_fallback():
    voice = {"playfulness": 0.2, "depth": 0.7, "romance": 0.1, "sentence_length": "short", "emoji_probability": 0.0}
    raw = "hello bonjour privet hello bonjour privet xyz xyz xyz"
    processed, flags = post_process_response(raw, voice, [], "چی؟")
    assert flags["garbage_filter_triggered"] is True
    assert is_garbage_output(raw)
    assert "bonjour" not in processed
    assert "privet" not in processed


def test_repetition_same_ending_not_reused_within_recent_messages():
    voice = {"playfulness": 0.1, "depth": 0.8, "romance": 0.1, "sentence_length": "medium", "emoji_probability": 0.0}
    recent = ["می‌فهمم. دوباره بگو ببینم چی می‌خواستی بپرسی؟"]
    processed, flags = post_process_response("باشه. دوباره بگو ببینم چی می‌خواستی بپرسی؟", voice, recent, "سلام")
    assert flags["repetition_filter_triggered"] is True
    assert processed != "باشه. دوباره بگو ببینم چی می‌خواستی بپرسی؟"
