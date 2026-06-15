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


def test_quality_gate_rejects_half_sentence_to_contextual_fallback():
    situation = detect_situation("حسابام بسته شدن", ["حقوقمو نریختن نتونستم چک رو پر کنم"])
    result = apply_quality_gate("ای بابا، یعنی", "chat", [], situation, "حسابام بسته شدن", ["حقوقمو نریختن نتونستم چک رو پر کنم"], {"name": "آذر"})
    assert result.rejected is True
    assert "حسابات" in result.final_text
