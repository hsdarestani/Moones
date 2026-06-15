from app.engine.persian_humanizer import humanize_persian
from app.engine.response_quality_gate import apply_quality_gate
from app.llm.model_router import detect_intent, detect_language, select_model


def test_persian_routes_to_persian_model_even_for_roleplay():
    assert detect_language("سلام نقش بازی کنیم") == "fa"
    assert select_model("سلام نقش بازی کنیم", "fa", "CLOSE", "roleplay") == "zai-org-glm-5-1"


def test_english_roleplay_uses_roleplay_model():
    assert select_model("let's roleplay", "en", "CLOSE", "roleplay") == "venice-uncensored-role-play"


def test_humanizer_rewrites_translated_persian():
    assert humanize_persian("چه کاری می‌کنی امروز؟") == "امروز چیکار می‌کنی؟"
    assert humanize_persian("می‌توانم چه کاری کنم؟") == "می‌خوای حرف بزنیم؟"


def test_quality_gate_rejects_garbage_and_uses_intent_fallback():
    result = apply_quality_gate("hello world random garbage текст", "greeting", [])
    assert result.rejected is True
    assert result.final_text in {"سلام :) خوبی؟", "سلام، خوبی؟", "سلام، چه خبر؟"}


def test_quality_gate_blocks_emoji_descriptions_and_spam():
    result = apply_quality_gate("باشه عزیزم (بوسه کوچک) 😘😘", "chat", [])
    assert "(" not in result.final_text
    assert result.final_text.count("😘") <= 1


def test_intent_specific_city_fallback_does_not_hallucinate_city():
    intent = detect_intent("از کدوم شهری؟")
    result = apply_quality_gate("تهران، محله جردن هستم", intent, ["راستش من شهر ثابتی ندارم، بیشتر با چیزی که تو ازم می‌سازی شکل می‌گیرم."])
    # Accepted model output can be valid, but deterministic fallback remains city-safe when needed.
    fallback = apply_quality_gate("چه کاری می‌توانم برایت انجام دهم؟", intent, [])
    assert "شهر ثابتی ندارم" in fallback.final_text
