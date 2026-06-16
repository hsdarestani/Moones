from app.engine.persian_humanizer import humanize_persian
from app.engine.response_quality_gate import apply_quality_gate
from app.llm.model_router import detect_intent, detect_language, select_model


def test_persian_routes_to_persian_model_even_for_roleplay():
    assert detect_language("سلام نقش بازی کنیم") == "fa"
    assert select_model("سلام نقش بازی کنیم", "fa", "CLOSE", "roleplay") == "qwen-3-6-plus"


def test_english_roleplay_uses_roleplay_model():
    assert select_model("let's roleplay", "en", "CLOSE", "roleplay") == "venice-uncensored-role-play"


def test_humanizer_rewrites_translated_persian():
    assert humanize_persian("چه کاری می‌کنی امروز؟") == "امروز چیکار می‌کنی؟"
    assert humanize_persian("می‌توانم چه کاری کنم؟") == "می‌خوای حرف بزنیم؟"


def test_quality_gate_rejects_garbage_without_rewriting():
    text = "hello world random garbage текст"
    result = apply_quality_gate(text, "greeting", [])
    assert result.rejected is True
    assert result.final_text == text


def test_quality_gate_flags_emoji_descriptions_without_rewriting():
    text = "باشه عزیزم (بوسه کوچک) 😘😘"
    result = apply_quality_gate(text, "chat", [])
    assert result.rejected is True
    assert result.final_text == text


def test_intent_specific_city_fallback_does_not_hallucinate_city():
    intent = detect_intent("از کدوم شهری؟")
    result = apply_quality_gate("تهران، محله جردن هستم", intent, ["راستش من شهر ثابتی ندارم، بیشتر با چیزی که تو ازم می‌سازی شکل می‌گیرم."])
    assert result.accepted is True
    rejected = apply_quality_gate("چه کاری می‌توانم برایت انجام دهم؟", intent, [])
    assert rejected.rejected is True
    assert rejected.final_text == "چه کاری می‌توانم برایت انجام دهم؟"
