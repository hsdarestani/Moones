from app.engine.context_aware_fallback import context_aware_fallback, simple_profile_answer
from app.engine.response_quality_gate import apply_quality_gate
from app.engine.situation_detector import detect_situation


def test_detects_bounced_cheque_financial_stress():
    situation = detect_situation("دلم گرفته امروز چک برگشتی داشتم")
    assert situation["intent"] == "financial_stress"
    assert situation["severity"] >= 0.8
    assert "چک برگشتی" in situation["entities"]


def test_detects_blocked_accounts_with_context():
    situation = detect_situation("حسابام بسته شدن", ["حقوقمو نریختن نتونستم چک رو پر کنم"])
    assert situation["intent"] == "legal_or_banking_problem"
    assert situation["severity"] >= 0.9
    assert "حسابام" in situation["entities"]


def test_context_aware_fallback_mentions_salary_cheque_and_blocked_accounts():
    situation = detect_situation("حسابام بسته شدن", ["حقوقمو نریختن نتونستم چک رو پر کنم"])
    text = context_aware_fallback(situation, "حسابام بسته شدن", ["حقوقمو نریختن نتونستم چک رو پر کنم"])
    assert "حقوقت" in text
    assert "چکت" in text
    assert "حسابات" in text


def test_profile_answer_is_deterministic():
    assert simple_profile_answer("اسمت چیه", {"name": "آذر"}) == "من آذرم :)"


def test_quality_gate_rejects_half_sentence_without_rewriting():
    situation = detect_situation("حسابام بسته شدن", ["حقوقمو نریختن نتونستم چک رو پر کنم"])
    text = "ای بابا، یعنی"
    result = apply_quality_gate(text, "chat", [], situation, "حسابام بسته شدن", ["حقوقمو نریختن نتونستم چک رو پر کنم"], {"name": "آذر"})
    assert result.rejected is True
    assert result.final_text == text


def test_simple_casual_intents_do_not_become_distress():
    examples = {
        "سلام": "greeting",
        "مرسی تو چطوری": "casual_checkin",
        "چی": "clarification",
        "امروز رفته بودم سینما": "casual_life_update",
        "چیزی اذیتم نمیکنه": "bot_complaint",
    }
    for message, intent in examples.items():
        assert detect_situation(message)["intent"] == intent


def test_old_financial_context_does_not_infer_from_vague_emotion():
    situation = detect_situation("امروز حالم بده", ["چک برگشتی داشتم"])
    assert situation["intent"] == "emotional_distress"
    assert situation["context_used"] is False


def test_financial_stress_records_matched_keywords_and_reason():
    situation = detect_situation("پول ندارم قسطمو بدم")
    assert situation["intent"] == "financial_stress"
    assert "پول ندارم" in situation["matched_keywords"]
    assert situation["why"] == "current_message_rule"


def test_contextual_generic_emotional_fallback_not_used_for_casual_context():
    situation = detect_situation("امروز رفته بودم سینما", ["سلام"])
    text = context_aware_fallback(situation, "امروز رفته بودم سینما", ["سلام"])
    assert "کدوم بخشش بیشتر اذیتت می‌کنه" not in text


def test_simple_intent_replies_are_deterministic():
    from app.engine.context_aware_fallback import simple_intent_reply

    profile = {"name": "حسین"}
    assert simple_intent_reply("سلام", detect_situation("سلام"), profile) == "سلام :) خوبی؟"
    assert simple_intent_reply("مرسی تو چطوری", detect_situation("مرسی تو چطوری"), profile) == "من خوبم، تو چطوری؟"
    assert simple_intent_reply("چی", detect_situation("چی"), profile) == "هیچی، بد گفتم. تو بگو :)"
    assert simple_intent_reply("امروز رفته بودم سینما", detect_situation("امروز رفته بودم سینما"), profile) == "عه چه خوب. چی دیدی؟"
    assert simple_intent_reply("چیزی اذیتم نمیکنه", detect_situation("چیزی اذیتم نمیکنه"), profile) == "آها، پس من اشتباه گرفتم."
